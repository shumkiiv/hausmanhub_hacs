import { renderHomeSection } from "./hausman-hub-home-sections.js?v=1.52.140";
import { renderFirstRunRoom } from "./hausman-hub-room-setup.js?v=1.52.140";
import { renderFirstRunDeviceGroups } from "./hausman-hub-room-device-groups.js?v=1.52.140";
import { resolveControlChannelTest, summarizeControlChannelReceipts } from "./hausman-hub-control-channel.js?v=1.52.140";
import { renderFirstRunClimateSources } from "./hausman-hub-room-climate-sources.js?v=1.52.140";
import { renderDeviceInventory } from "./hausman-hub-device-inventory.js?v=1.52.140";
import { loadDeviceBindings, renderDeviceBindingCallout, renderDeviceBindings } from "./hausman-hub-device-bindings.js?v=1.52.140";
import { closeFirstRunAreaCreator, createFirstRunArea, openFirstRunAreaCreator, renderFirstRunAreaBinding } from "./hausman-hub-area-binding.js?v=1.52.140";
import { createKioskButton, createKioskDock, handleKioskPointerUp, openIntercomFromRail, openRoomFromOverview, PANEL_SECTIONS, renderOverviewNavigationSummary, resolveIntercomQuickAction, restoreNavigationFromLocation, SECTION_SUBTITLES, setKioskState, writeNavigationRoute } from "./hausman-hub-navigation.js?v=1.52.140";
import { loadEnergyHistory, renderEnergyOverviewCard, renderEnergySection, saveEnergySettings } from "./hausman-hub-energy.js?v=1.52.140";
import { loadEnergyMeter } from "./hausman-hub-energy-meter.js?v=1.52.140";
import { loadDeviceDiscovery, updateDeviceDiscoveryBadge } from "./hausman-hub-device-discovery.js?v=1.52.140";
import { redrawEnergyChartsForTheme } from "./hausman-hub-energy-chart.js?v=1.52.140";
import { applyHomeSignalSelection, AWAY_MODE_EXPLANATION, AWAY_MODE_TYPE, createHeatingTemperatureFields, createPriorityChoicePicker, homeEnvironmentSaveError, homeEnvironmentSourcePayload, HOME_SIGNAL_BINDINGS, isAwayModeCandidate, isCentralHeatingCandidate, signalCandidateDisplayName } from "./hausman-hub-weather-sources.js?v=1.52.140";
import { renderMediaDeviceCard } from "./hausman-hub-media-device.js?v=1.52.140";
import { renderScenarioSection } from "./hausman-hub-scenarios.js?v=1.52.140";
import { renderClimateOverview, setClimateManualMode } from "./hausman-hub-climate-overview.js?v=1.52.140";
import { renderLightingOverview } from "./hausman-hub-lighting.js?v=1.52.140";
import { renderRoomsOverview } from "./hausman-hub-rooms.js?v=1.52.140";
import { renderMediaOverview } from "./hausman-hub-media-overview.js?v=1.52.140";
import { renderSecurityOverview } from "./hausman-hub-security-overview.js?v=1.52.140";
import { renderDevicesOverview } from "./hausman-hub-devices-overview.js?v=1.52.140";
import { buildDiagnosticChecks, diagnosticSummaryText, renderDiagnosticDetails } from "./hausman-hub-diagnostics.js?v=1.52.140";
import { renderRolloutReadiness } from "./hausman-hub-rollout.js?v=1.52.140";
import { overviewHeroRenderKey } from "./hausman-hub-overview-hero-state.js?v=1.52.140";
import { formatUpcomingCountdown, renderOverviewContent, renderOverviewHero } from "./hausman-hub-overview.js?v=1.52.140";
import { conciseDeviceActionLabel, renderPhysicalDeviceCard } from "./hausman-hub-device-card.js?v=1.52.140";
import { recordTechnicalEvent as log, renderTechnicalLogCard } from "./hausman-hub-technical-log.js?v=1.52.140";
import { applyFeedback } from "./hausman-hub-feedback.js?v=1.52.140";
import { apiErrorMessage, resolveApiError } from "./hausman-hub-error-taxonomy.js?v=1.52.140";
import { canExecuteCommand, loadingUiState, offlineUiState, staleUiState } from "./hausman-hub-ui-state.js?v=1.52.140";
import { filterCatalogActions, loadDeviceFeatureMatrix } from "./hausman-hub-device-features.js?v=1.52.140";
import { withCorrelationId } from "./hausman-hub-correlation.js?v=1.52.140";
import { createEventStreamClient, createFetchEventSource, EVENT_STREAM_PATH, recordActivityEvent, resolveEventStreamToken } from "./hausman-hub-pagination.js?v=1.52.140";
import { renderKiosk } from "./hausman-hub-kiosk.js?v=1.52.140";
import { captureRoomValidation, clearFirstRunDraft, persistFirstRunDraft, reconcileRoomValidation, restoreFirstRunDraft, resumeFirstRunDraft } from "./hausman-hub-first-run-draft.js?v=1.52.140";
import { applyTabletProfile, isIntercomQuickAccessVisible, renderAppearanceSettings, renderIntercomSettings, syncIntercomQuickAccess } from "./hausman-hub-settings-profile.js?v=1.52.140";
import { renderSettingsRooms, saveRoomType } from "./hausman-hub-settings-rooms.js?v=1.52.140";
import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.140";

const PANEL_API = "hausman_hub/v1/admin/panel";
const PANEL_CSS_URL = "/api/hausman_hub/panel/hausman-hub-panel.css?v=1.52.140";
const DASHBOARD_API = "hausman_hub/v1/dashboard";
const CLIMATE_RUNTIME_API = "hausman_hub/v1/climate/runtime";
const DEVICE_ACTIONS_API = "hausman_hub/v1/device-actions";
const CAPABILITIES_API = "hausman_hub/v1/capabilities";
const PANEL_TABLET_PROFILE_API = "hausman_hub/v1/tablet-profile";
const MODE_API = "hausman_hub/v1/admin/climate-mode";
const HOME_API = "hausman_hub/v1/admin/home-environment";
const WINDOWS_API = "hausman_hub/v1/admin/climate-room-signals";
const DRAFT_API = "hausman_hub/v1/admin/climate-drafts";
const SETUP_API = "hausman_hub/v1/admin/climate-drafts/current";
const DRAFT_VALIDATE_API = `${DRAFT_API}/validate`;
const DRAFT_SAVE_API = `${DRAFT_API}/save`;
const AREA_ASSIGNMENTS_API = "hausman_hub/v1/admin/device-area-assignments";
const PROFILES_API = "hausman_hub/v1/admin/climate-profiles";
const SCHEDULE_API = "hausman_hub/v1/admin/climate-schedule";
const AI_ASSISTANT_API = "hausman_hub/v1/admin/ai-assistant";
const AI_ASSISTANT_SETTINGS_API = `${AI_ASSISTANT_API}/settings`;
const AI_ASSISTANT_REFRESH_API = `${AI_ASSISTANT_API}/refresh`;
const SCENARIOS_API = "hausman_hub/v1/admin/scenarios";
const SCENARIOS_CATALOG_API = "hausman_hub/v1/admin/scenarios/catalog";
const SCENARIOS_TEST_API = "hausman_hub/v1/admin/scenarios/test";
const SCENARIOS_DELETE_API = "hausman_hub/v1/admin/scenarios/delete";
const SCENARIOS_RUN_API = "hausman_hub/v1/admin/scenarios/run";
const SCENARIOS_UPCOMING_API = "hausman_hub/v1/scenarios/upcoming";
const SCENARIOS_UPCOMING_CANCEL_API = "hausman_hub/v1/scenarios/upcoming/cancel";
const CONNECTION_SETTINGS_API = "hausman_hub/v1/admin/connection-settings";
const RESET_API = "hausman_hub/v1/admin/reset";
const USER_PREFERENCES_KEY = "hausman_hub";
const IR_CODES_API = "hausman_hub/v1/admin/ir-codes";
const IR_CODES_SCAN_API = `${IR_CODES_API}/scan`;
const IR_CODES_LEARN_API = `${IR_CODES_API}/learn`;
const IR_CODES_TEST_API = `${IR_CODES_API}/test`;
const IR_CODES_DELETE_API = `${IR_CODES_API}/delete`;
const IR_CODE_BINDINGS_API = `${IR_CODES_API}/bindings`;
const REFRESH_MS = 30000;
const STALE_MS = REFRESH_MS * 3;
const LOCKOUT_HELP = "Теплее верхнего порога — нагрев запрещён; холоднее нижнего — разрешён. Между ними режим не меняется.";

const PROFILE_CONTRACT = { name: "hausman-hub-climate-profile-update-request", version: 1 };
const SCHEDULE_CONTRACT = { name: "hausman-hub-climate-schedule-update-request", version: 1 };
const STRATEGY_ORDER = ["soft", "normal", "aggressive"];
const CONTOUR_MODE_ORDER = ["observe", "automatic"];
const ACTIVE_DEVICE_TYPES = new Set([
  "air_conditioner", "radiator_thermostat", "humidifier", "floor_heating",
]);
const SENSOR_DEVICE_TYPES = new Set(["temperature_sensor", "humidity_sensor"]);
const ROOM_PRESENCE_DEVICE_CLASSES = new Set(["motion", "occupancy", "presence"]);
const CONTROL_CHANNEL_LABELS = {
  universal_ir: "Универсальный ИК-пульт",
  yandex_remote: "Пульт Яндекса",
  direct_wifi: "Напрямую через Home Assistant",
};
const FIRST_RUN_STEPS = [
  "rooms", "room", "home", "validation", "save", "code_source", "tablet", "completion", "success",
];
const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;
const ZIGBEE2MQTT_IMAGE_PATTERN =
  /^https:\/\/www\.zigbee2mqtt\.io\/images\/devices\/(?:[A-Za-z0-9._~-]|%[0-9A-F]{2})+\.png$/;
const OUTDOOR_IDENTITY_PATTERN =
  /outdoor|outside|external|exterior|street|yard|улич|улиц|наруж|внешн|двор|погод/;
const CLIMATE_VIEWS = [
  { id: "overview", label: "Обзор", subtitle: "Климат по комнатам и отдельным устройствам" },
  { id: "contour", label: "Контур", subtitle: "Контур и управление климатом" },
  { id: "profiles", label: "Профили", subtitle: "Профили и расписание комфорта" },
  { id: "schedule", label: "Расписание", subtitle: "Границы профилей и автоматизация" },
  { id: "home", label: "Сигналы дома", subtitle: "Сигналы дома и отопление" },
  { id: "windows", label: "Сигналы комнат", subtitle: "Окна и присутствие по комнатам" },
  { id: "assistant", label: "Умный помощник", subtitle: "Советы и статистика" },
];
const SETTINGS_VIEWS = [
  { id: "overview", label: "Обзор", description: "Главные параметры и состояние" },
  { id: "rooms", label: "Комнаты", description: "Комнаты, устройства и датчики" },
  { id: "bindings", label: "Привязки", description: "Сущности Home Assistant" },
  { id: "connection", label: "Подключение", description: "Связь с Home Assistant" },
  { id: "intercom", label: "Домофон", description: "Устройство и быстрый доступ" },
  { id: "appearance", label: "Интерфейс", description: "Тема, анимация и подсказки" },
  { id: "system", label: "Диагностика", description: "Связь, компоненты и безопасное обслуживание" },
];
const ROOM_SETUP_PANES = [
  { id: "devices", label: "Устройства", description: "Чем измерять и управлять" },
  { id: "comfort", label: "Комфорт", description: "Цели температуры и влажности" },
  { id: "schedule", label: "Режим дня", description: "Когда включать дневные и ночные цели" },
  { id: "limits", label: "Защита", description: "Безопасные границы температуры" },
  { id: "review", label: "Проверка", description: "Что будет сохранено" },
];
const READINESS_LABELS = {
  ready: "Система готова к управлению",
  not_ready: "Нужна настройка системы",
  unavailable: "Система временно недоступна",
  disabled: "Управление климатом выключено",
};
const BRIDGE_MODE_LABELS = {
  disabled: "Выключен",
  managed: "Автоматическое управление",
  native: "Встроенный контур",
  shadow: "Только наблюдение",
  canary: "Пилотная комната",
};
const SETUP_STATUS_LABELS = {
  not_configured: "Не настроен",
  ready: "Готов",
  attention: "Требует внимания",
};
const LOCAL_DISPLAY_NAMES = {
  blocked_reasons: {
    bridge_disabled: "Контур выключен в настройках", shadow_only: "Включена только проверка без команд",
    room_not_selected: "Комната не выбрана для управления", state_stale: "Данные о климате устарели",
    registry_mismatch: "Настройка устройств не совпадает", authority_not_ready: "Контур не готов к управлению",
    device_unavailable: "Устройство недоступно", actions_unsupported: "Устройство не поддерживает нужные действия",
    evidence_not_ready: "Проверка ещё не завершена", operation_pending: "Команда ещё проверяется", needs_reimport: "Устройство нужно подключить заново"
  },
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function requestId(prefix) {
  const random = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now()}-${random}`.slice(0, 64);
}

function selectField(options, current, onChange) {
  const select = el("select");
  options.forEach((item) => {
    const option = el("option", null, item.label);
    option.value = item.value;
    select.appendChild(option);
  });
  select.value = current === null || current === undefined ? "" : String(current);
  select.addEventListener("change", onChange);
  return select;
}

function numberField(value, min, max, step, onChange) {
  const input = el("input");
  input.type = "number";
  input.min = min;
  input.max = max;
  input.step = step;
  input.value = value;
  input.addEventListener("input", onChange);
  return input;
}

function setAttr(node, name, value) {
  if (typeof node.setAttribute === "function") node.setAttribute(name, String(value));
  else node[name] = String(value);
}

const SVG_NAMESPACE = "http" + "://www.w3.org/2000/svg";

const ICON_PATHS = {
  dashboard: "M3 13h8V3H3zm0 8h8v-6H3zm10 0h8V11h-8zm0-18v6h8V3z",
  lightbulb: "M9 21h6v-1H9zm3-19a7 7 0 0 0-4 12.74V17h8v-2.26A7 7 0 0 0 12 2zm2.85 11.1-.85.51V15h-4v-1.39l-.85-.51A5 5 0 1 1 14.85 13.1z",
  "ceiling-light": "M12 4C6.48 4 2 7.58 2 12s4.48 8 10 8 10-3.58 10-8-4.48-8-10-8m0 2c4.42 0 8 2.69 8 6s-3.58 6-8 6-8-2.69-8-6 3.58-6 8-6m-5 6h10v2H7z",
  rooms: "M3 21V3h10v4h8v14h-2v-2h-4v2h-2v-8H5v8zm2-10h6V5H5zm10 6h4v-2h-4zm0-4h4v-2h-4z",
  media: "M4 6h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2m0 2v10h16V8zm6 2 5 3-5 3z",
  shield: "M12 2 4 5v6c0 5.05 3.41 9.74 8 11 4.59-1.26 8-5.95 8-11V5zm0 2.18L18 6.43V11c0 3.93-2.55 7.76-6 8.92C8.55 18.76 6 14.93 6 11V6.43z",
  check: "M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z",
  lock: "M17 8h-1V6a4 4 0 0 0-8 0v2H7a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2m-7-2a2 2 0 1 1 4 0v2h-4zm7 13H7v-9h10z",
  window: "M3 3h18v18H3zm1 1v7h7V4zm9 0v7h7V4zM4 12v7h7v-7zm9 0v7h7v-7z",
  door: "M6 3h12v18H6zm8 7a1.2 1.2 0 1 0 .1 0z",
  alarm: "M6 18h12v2H6zm2-2V9a4 4 0 0 1 8 0v7zm2-2h4V9a2 2 0 0 0-4 0zM3 8l3-3 1.4 1.4-3 3zm18 0-1.4 1.4-3-3L18 5z",
  camera: "M9 4 7.17 6H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-3.17L15 4zm3 13a4 4 0 1 1 0-8 4 4 0 0 1 0 8m0-2a2 2 0 1 0 0-4 2 2 0 0 0 0 4",
  settings: "M19.43 12.98c.04-.32.07-.65.07-.98s-.03-.66-.08-.98l2.11-1.65-2-3.46-2.49 1a7.2 7.2 0 0 0-1.69-.98L15 3.27h-4l-.35 2.66c-.61.25-1.17.59-1.69.98l-2.49-1-2 3.46 2.11 1.65c-.05.32-.08.66-.08.98s.03.66.08.98l-2.11 1.65 2 3.46 2.49-1c.52.4 1.08.73 1.69.98L11 20.73h4l.35-2.66c.61-.25 1.17-.58 1.69-.98l2.49 1 2-3.46zM13 17a5 5 0 1 1 0-10 5 5 0 0 1 0 10m0-3a2 2 0 1 0 0-4 2 2 0 0 0 0 4",
  chevron: "M16.59 8.59 12 13.17 7.41 8.59 6 10l6 6 6-6z",
  "chevron-left": "M15.41 16.59 10.83 12l4.58-4.59L14 6l-6 6 6 6z",
  "chevron-right": "M8.59 16.59 13.17 12 8.59 7.41 10 6l6 6-6 6z",
  device: "M4 6h18V4H4c-1.1 0-2 .9-2 2v11H0v3h14v-3H4zm19 2h-6c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h6c.55 0 1-.45 1-1V9c0-.55-.45-1-1-1m-1 9h-4v-7h4z",
  warning: "M1 21h22L12 2zm12-3h-2v-2h2zm0-4h-2v-4h2z",
  trash: "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6zm3.46-7.12 1.41-1.41L12 11.59l1.12-1.12 1.41 1.41L13.41 13l1.12 1.12-1.41 1.41L12 14.41l-1.12 1.12-1.41-1.41L10.59 13zM15.5 4l-1-1h-5l-1 1H5v2h14V4z",
  sun: "M6.76 4.84l-1.8-1.79-1.41 1.41 1.79 1.79 1.42-1.41zM4 10.5H1v2h3v-2zm9-9.95h-2V3.5h2V.55zm7.45 3.91l-1.41-1.41-1.79 1.79 1.41 1.41 1.79-1.79zm-3.21 13.7l1.79 1.8 1.41-1.41-1.8-1.79-1.4 1.4zM20 10.5v2h3v-2h-3zm-8-5c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm-1 16.95h2V19.5h-2v2.95zm-7.45-3.91l1.41 1.41 1.79-1.8-1.41-1.41-1.79 1.8z",
  moon: "M12 3c-4.97 0-9 4.03-9 9s4.03 9 9 9 9-4.03 9-9c0-.46-.04-.92-.1-1.36-.98 1.37-2.58 2.26-4.4 2.26-2.98 0-5.4-2.42-5.4-5.4 0-1.81.89-3.42 2.26-4.4-.44-.06-.9-.1-1.36-.1z",
  auto: "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18V4c4.41 0 8 3.59 8 8s-3.59 8-8 8z",
  manual: "M9 11V5a1 1 0 0 1 2 0v5h1V3a1 1 0 0 1 2 0v7h1V4a1 1 0 0 1 2 0v8h1V7a1 1 0 0 1 2 0v7a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5v-3a1 1 0 0 1 2 0z",
  home: "M12 3 2 12h3v9h6v-6h2v6h6v-9h3L12 3zm0 2.69L18 11v8h-3v-6H9v6H6v-8l6-5.31z",
  "home-filled": "M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z",
  thermometer: "M15 13V5a3 3 0 0 0-6 0v8a5 5 0 1 0 6 0zm-3 6a3 3 0 0 1-1-5.83V5a1 1 0 0 1 2 0v8.17A3 3 0 0 1 12 19z",
  water: "M12 2.69 6.35 8.34A8 8 0 0 0 4 14a8 8 0 0 0 16 0c0-2.21-.9-4.21-2.35-5.66L12 2.69zM12 20a6 6 0 0 1-4.24-10.24L12 5.52l4.24 4.24A6 6 0 0 1 12 20z",
  bolt: "M11 21h-1l1-7H7.5c-.88 0-.33-.75-.31-.78C8.46 10.97 10.37 7.63 13 3h1l-1 7h3.5c.4 0 .62.19.4.66C12.97 17.53 11 21 11 21z",
  energy: "",
  play: "M8 5v14l11-7z",
  intercom: "M7 2h10a3 3 0 0 1 3 3v14a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V5a3 3 0 0 1 3-3m0 2a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V5a1 1 0 0 0-1-1zm5 2a3 3 0 1 1 0 6 3 3 0 0 1 0-6m-3 8h6v2H9zm0 4h4v2H9z",
  close: "M6.4 5 5 6.4 10.6 12 5 17.6 6.4 19l5.6-5.6 5.6 5.6 1.4-1.4-5.6-5.6L19 6.4 17.6 5 12 10.6z",
  refresh: "M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z",
  star: "M12 17.27 18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z",
  more: "M12 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4m0 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4m0 6a2 2 0 1 0 0 4 2 2 0 0 0 0-4",
  fullscreen: "M7 14H5v5h5v-2H7zm-2-4h2V7h3V5H5zm12 7h-3v2h5v-5h-2zm-3-12v2h3v3h2V5z",
  history: "M13 3a9 9 0 0 0-8.95 8H1l4 4 4-4H6.06A7 7 0 1 1 8 16.9l-1.42 1.42A9 9 0 1 0 13 3m-1 5v5l4.25 2.52 1-1.64-3.25-1.93V8z",
  air: "M4 10h10.5a2.5 2.5 0 1 0-2.45-3H10a4.5 4.5 0 1 1 4.5 5H4zm0 4h13.5a4.5 4.5 0 1 1-4.5 4.5h2a2.5 2.5 0 1 0 2.5-2.5H4zm0-8h4v2H4z",
  leaf: "M17.5 3C12 3 7 6.58 7 12c0 1.57.45 3.04 1.24 4.28L5 19.5 6.5 21l3.2-3.2A8.9 8.9 0 0 0 13 18c5.42 0 9-5 9-10.5V3zm-4.33 12.93c-.75 0-1.47-.13-2.13-.38 1.13-2.38 3-4.42 5.35-5.73-2.83.66-5.24 2.3-6.87 4.55A6.2 6.2 0 0 1 9 12c0-3.78 3.35-6.73 8.5-7H20v2.5c0 5.15-2.95 8.43-6.83 8.43",
  wifi: "M12 18.5 9.5 16a3.54 3.54 0 0 1 5 0zM7.05 13.55l-2.5-2.5a10.54 10.54 0 0 1 14.9 0l-2.5 2.5a7 7 0 0 0-9.9 0M2.5 9 0 6.5a16.97 16.97 0 0 1 24 0L21.5 9a13.44 13.44 0 0 0-19 0",
  cloud: "M19.35 10.04A7.5 7.5 0 0 0 5.3 8.04 6 6 0 0 0 6 20h13a5 5 0 0 0 .35-9.96M19 18H6a4 4 0 0 1-.15-8A5.5 5.5 0 0 1 16.9 11.1 3 3 0 1 1 19 18",
  battery: "M17 5H3a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-3h2v-4h-2V7a2 2 0 0 0-2-2m0 12H3V7h14z",
};

const ICON_STROKE_PATHS = {
  energy: [
    "M8 2.75h8c1.8 0 3.25 1.45 3.25 3.25v12c0 1.8-1.45 3.25-3.25 3.25H8c-1.8 0-3.25-1.45-3.25-3.25V6C4.75 4.2 6.2 2.75 8 2.75Z",
    "M8 10.5c0-2.35 1.78-4 4-4s4 1.65 4 4",
    "M12 10.5l2.35-2.35",
    "M8.5 16h1.65l1.2-1.9L13 18l1.2-2h1.3",
  ],
};

const THEME_MODES = ["auto", "daynight", "light", "dark"];
const DAYNIGHT_DAY_START_HOUR = 7;
const DAYNIGHT_NIGHT_START_HOUR = 22;
const HEADER_CLOCK_REFRESH_MS = 60000;
const HEADER_CLOCK_DATE_FORMAT = new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long" });
const HEADER_CLOCK_TIME_FORMAT = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });
const THEME_MODE_META = {
  auto: { icon: "auto", label: "Тема: авто (следует Home Assistant)", hint: "авто" },
  daynight: { icon: "sun", label: "Тема: день/ночь (по времени суток)", hint: "день/ночь" },
  light: { icon: "sun", label: "Тема: светлая", hint: "светлая" },
  dark: { icon: "moon", label: "Тема: тёмная", hint: "тёмная" },
};

function isDaytimeNow(now = new Date()) {
  const hour = now.getHours();
  return hour >= DAYNIGHT_DAY_START_HOUR && hour < DAYNIGHT_NIGHT_START_HOUR;
}

function msUntilNextDaynightBoundary(now = new Date()) {
  const boundaryHour = isDaytimeNow(now) ? DAYNIGHT_NIGHT_START_HOUR : DAYNIGHT_DAY_START_HOUR;
  const next = new Date(now.getTime());
  next.setHours(boundaryHour, 0, 30, 0);
  if (next.getTime() <= now.getTime()) next.setDate(next.getDate() + 1);
  return next.getTime() - now.getTime();
}

function svgIcon(name, className) {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  setAttr(svg, "viewBox", "0 0 24 24");
  setAttr(svg, "aria-hidden", "true");
  setAttr(svg, "focusable", "false");
  setAttr(svg, "class", className ? `icon ${className}` : "icon");
  const strokePaths = ICON_STROKE_PATHS[name];
  if (strokePaths) {
    strokePaths.forEach((data) => {
      const path = document.createElementNS(SVG_NAMESPACE, "path");
      setAttr(path, "d", data);
      setAttr(path, "fill", "none");
      setAttr(path, "stroke", "currentColor");
      setAttr(path, "stroke-width", "1.8");
      setAttr(path, "stroke-linecap", "round");
      setAttr(path, "stroke-linejoin", "round");
      svg.appendChild(path);
    });
  } else {
    const path = document.createElementNS(SVG_NAMESPACE, "path");
    setAttr(path, "d", ICON_PATHS[name]);
    setAttr(path, "fill", "currentColor");
    svg.appendChild(path);
  }
  return svg;
}

function brandMark() {
  const mark = el("span", "brand-mark");
  setAttr(mark, "aria-hidden", "true");
  const shell = document.createElementNS(SVG_NAMESPACE, "svg");
  setAttr(shell, "viewBox", "0 0 31.7778 37.0833");
  setAttr(shell, "width", "32");
  setAttr(shell, "height", "38");
  setAttr(shell, "class", "brand-mark-shell");
  const shellPath = document.createElementNS(SVG_NAMESPACE, "path");
  setAttr(shellPath, "d", "M1.66667 35.4167V12.75L15.8889 2.08333L30.1111 12.75V35.4167H1.66667Z");
  setAttr(shellPath, "fill", "none");
  setAttr(shellPath, "stroke", "currentColor");
  setAttr(shellPath, "stroke-width", "3.33333");
  setAttr(shellPath, "stroke-linecap", "square");
  shell.appendChild(shellPath);
  const letter = document.createElementNS(SVG_NAMESPACE, "svg");
  setAttr(letter, "viewBox", "0 0 17.1111 15.3333");
  setAttr(letter, "width", "18");
  setAttr(letter, "height", "16");
  setAttr(letter, "class", "brand-mark-letter");
  const letterPath = document.createElementNS(SVG_NAMESPACE, "path");
  setAttr(letterPath, "d", "M1.88889 1.88889V13.4444M15.2222 1.88889V13.4444M1.88889 7.66667H15.2222");
  setAttr(letterPath, "fill", "none");
  setAttr(letterPath, "stroke", "currentColor");
  setAttr(letterPath, "stroke-width", "3.77778");
  setAttr(letterPath, "stroke-linecap", "square");
  letter.appendChild(letterPath);
  mark.appendChild(shell);
  mark.appendChild(letter);
  return mark;
}

function focusNode(node) {
  if (node && typeof node.focus === "function") node.focus();
}

function normalizedText(value) {
  return String(value || "").trim().toLocaleLowerCase("ru");
}

class HausmanHubPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._data = null;
    this._homeDashboard = null;
    this._climateRuntime = null;
    this._climateModePendingKey = null;
    this._upcomingEvents = null;
    this._upcomingTimer = null;
    this._overviewHeroRoomId = null;
    this._settings = { mode: null, home: null, windows: null, setup: null };
    this._assistant = {
      data: null, error: false, fields: null, loaded: false, loading: false,
    };
    this._scenarios = { list: null, catalog: null, loading: false, error: false };
    this._tabletProfile = null;
    this._intercomDraft = { showQuickAccess: false, deviceId: null };
    this._intercomDirty = false;
    this._roomTypeSaving = new Set();
    this._scenarioEditor = null;
    this._settingsData = { connection_mode: "home_assistant", smart_home_center_url: "", home_assistant_url: "" };
    this._settingsBaseline = { ...this._settingsData };
    this._settingsPrefs = { large_text: false, reduced_motion: false, show_hints: true, rail_collapsed: false };
    this._settingsDirty = false;
    this._energyDraft = null;
    this._energySettingsOpen = false;
    this._energySelectedDeviceId = null;
    this._energyDetailsOpen = false;
    this._energyModalView = "overview";
    this._energyModalJustOpened = false;
    this._energyMeter = null;
    this._energyMeterDraft = null;
    this._energyMeterLoading = false;
    this._energyMeterSaving = false;
    this._energyMeterError = null;
    this._energyMeterNotice = "";
    this._deviceDiscovery = null;
    this._deviceDiscoveryLoading = false;
    this._deviceDiscoveryError = null;
    this._deviceDiscoveryPending = null;
    this._deviceDiscoveryMessages = {};
    this._deviceDiscoveryNotice = "";
    this._deviceDiscoveryAreaDrafts = {};
    this._deviceDiscoveryBadgeNode = null;
    this._energyHistory = null;
    this._energyTodayKwh = null;
    this._energyHistoryLoading = false;
    this._energyHistoryReloadRequested = false;
    this._energyHistoryPeriod = "day";
    this._energyHistoryError = null;
    this._deviceBindings = {
      data:null, error:false, loading: false, preview: null, selections: {},
      showOtherRooms: false, showConfigured: false, status: "", previewTimer: null,
    };
    this._resetArmed = false;
    this._error = false;
    this._busy = false;
    this._notice = "";
    this._loadedAt = 0;
    this._technicalLog = [];
    this._themeMode = "auto";
    this._daynightTimer = null;
    this._preferencesLoaded = false;
    this._preferencesLoading = false;
    this._preferencesDirty = false;
    this._preferencesWriting = false;
    this._timer = null;
    this._styleRevealTimer = null;
    this._shell = null;
    this._activeSection = null;
    this._activeClimateView = "overview";
    this._activeSettingsView = "overview";
    this._activeRoomSetupPane = "devices";
    this._expandedWizardRooms = new Set();
    this._openSignalPickers = new Set();
    this._openHomeCards = new Set();
    this._activeDeviceModalClose = null;
    this._activeDeviceModalKey = null;
    this._sectionRenderKeys = {};
    this._loadingPanel = false;
    this._kioskMode = false;
    this._kioskTapAt = 0;
    this._climateOverlay = null;
    this._mediaOverlay = null;
    this._dirty = {
      wizard: false, home: false, windows: false, profiles: false, schedule: false, mode: false,
      assistant: false,
    };
    this._wizard = {
      open: false,
      loading: false,
      options: null,
      optionsError: false,
      draft: null,
      validation: null,
      fingerprint: null,
      setupRevision: null,
    };
    this._wizardFields = null;
    this._wizardIssues = null;
    this._wizardButtons = null;
    this._firstRun = {
      areaAssignments: {},
      areaCreator: {
        error: "",
        name: "",
        open: false,
        status: "",
        targetGroupId: null,
      },
      areaSaveError: "",
      areaSaveStatus: "",
      completed: false,
      contourSaved: false,
      conflict: false,
      deferred: false,
      draft: null,
      fields: {},
      home: null,
      issues: [],
      ir: {
        activeDeviceId: null,
        broadlinkExpanded: false,
        codes: [],
        error: "",
        loading: false,
        manual: { deviceId: null, index: 0, statuses: {} },
        scan: null,
        smartir: { brand: "", deviceCode: "", commandName: "" },
      },
      loading: false,
      options: null,
      optionsError: false,
      roomId: null,
      rooms: {},
      showRoomDevices: false,
      schedule: { enabled: false, dayStart: "07:00", nightStart: "23:00" },
      setupRevision: null,
      step: "instructions",
      validRooms: new Set(),
      validation: null,
    };
    this._firstRunFields = null;
    this._firstRunDraftReady = false;
    this._persistFirstRunBeforeUnload = () => {
      if (this._firstRunDraftReady) persistFirstRunDraft(this);
    };
    this._onVisible = () => {
      if (document.hidden) {
        this._persistFirstRunBeforeUnload();
      } else {
        this._load();
      }
    };
    this._onNavigationPop = () => {
      restoreNavigationFromLocation(this, true, PANEL_SECTIONS, CLIMATE_VIEWS, SETTINGS_VIEWS);
      this._render();
      this._loadActiveNavigationView();
    };
    this._onFullscreenChange = () => {
      const active = typeof document !== "undefined" && document.fullscreenElement === this;
      setKioskState(this, active);
    };
    this._onKioskPointerUp = (event) => handleKioskPointerUp(this, event);
  }

  set hass(value) {
    const first = this._hass === null;
    this._hass = value;
    this._applyThemeMode();
    if (!this._preferencesLoaded) this._loadUserPreferences();
    if (first) {
      this._load();
    }
    this._startEventStream();
    this._loadActiveNavigationView();
  }

  connectedCallback() {
    restoreNavigationFromLocation(this, false, PANEL_SECTIONS, CLIMATE_VIEWS, SETTINGS_VIEWS);
    this._timer = setInterval(() => this._load(), REFRESH_MS);
    this._upcomingTimer = setInterval(() => this._refreshUpcomingCountdowns(), REFRESH_MS);
    if (this._clockTimer) clearInterval(this._clockTimer);
    this._clockTimer = setInterval(() => this._updateHeaderClock(), HEADER_CLOCK_REFRESH_MS);
    document.addEventListener("visibilitychange", this._onVisible);
    if (typeof document.addEventListener === "function") {
      document.addEventListener("fullscreenchange", this._onFullscreenChange);
    }
    if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
      window.addEventListener("popstate", this._onNavigationPop);
      window.addEventListener("pagehide", this._persistFirstRunBeforeUnload);
    }
    this.addEventListener?.("pointerup", this._onKioskPointerUp);
    this._startEventStream();
    this._render();
    this._loadActiveNavigationView();
  }

  /* Domain events refresh the snapshot immediately; the 30s polling timer */
  /* stays as fallback when the stream has no auth token or keeps failing. */
  _startEventStream() {
    if (this._eventStreamClient || !this._hass || !resolveEventStreamToken(this._hass)) return;
    this._eventStreamClient = createEventStreamClient({
      connect: createFetchEventSource(EVENT_STREAM_PATH, () => resolveEventStreamToken(this._hass)),
      onDomainEvent: (event) => { recordActivityEvent(this, event); this._load(); },
      onGap: () => this._load(),
    });
    this._eventStreamClient.start();
  }

  disconnectedCallback() {
    this._persistFirstRunBeforeUnload();
    if (this._eventStreamClient) {
      this._eventStreamClient.stop();
      this._eventStreamClient = null;
    }
    if (typeof this._activeDeviceModalClose === "function") this._activeDeviceModalClose();
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
    if (this._upcomingTimer) clearInterval(this._upcomingTimer);
    this._upcomingTimer = null;
    if (this._clockTimer) clearInterval(this._clockTimer);
    this._clockTimer = null;
    if (this._daynightTimer && typeof window !== "undefined" && typeof window.clearTimeout === "function") {
      window.clearTimeout(this._daynightTimer);
    }
    this._daynightTimer = null;
    if (this._styleRevealTimer && typeof window !== "undefined" && typeof window.clearTimeout === "function") {
      window.clearTimeout(this._styleRevealTimer);
    }
    this._styleRevealTimer = null;
    document.removeEventListener("visibilitychange", this._onVisible);
    if (typeof window !== "undefined" && typeof window.removeEventListener === "function") {
      window.removeEventListener("popstate", this._onNavigationPop);
      window.removeEventListener("pagehide", this._persistFirstRunBeforeUnload);
    }
    if (typeof document !== "undefined" && typeof document.removeEventListener === "function") {
      document.removeEventListener("fullscreenchange", this._onFullscreenChange);
    }
    this.removeEventListener?.("pointerup", this._onKioskPointerUp);
  }

  _loadActiveNavigationView() {
    if (
      this._activeSection === "scenarios"
      && !this._scenarios.list
      && !this._scenarios.loading
    ) {
      this._loadScenarios();
    }
    if (
      this._activeSection === "climate"
      && this._activeClimateView === "assistant"
      && !this._assistant.loaded
    ) {
      this._loadAssistant();
    }
    if (
      this._activeSection === "settings"
      && this._activeSettingsView === "bindings"
      && !this._deviceBindings.data
      && !this._deviceBindings.loading
      && !this._deviceBindings.error
    ) {
      loadDeviceBindings(this);
    }
    if (
      this._activeSection === "settings"
      && this._activeSettingsView === "system"
      && !this._scenarios.list
      && !this._scenarios.loading
    ) {
      this._loadScenarios();
    }
  }

  _applyThemeMode() {
    const darkMode = !!(this._hass && this._hass.themes && this._hass.themes.darkMode);
    let effective = this._themeMode;
    if (this._themeMode === "auto") effective = darkMode ? "dark" : "light";
    if (this._themeMode === "daynight") effective = isDaytimeNow() ? "light" : "dark";
    if (this.classList && typeof this.classList.toggle === "function") {
      this.classList.toggle("theme-light", effective === "light");
    }
    redrawEnergyChartsForTheme(this);
    this._updateThemeSwitcher();
    this._applyLocalPreferences();
    this._scheduleDaynightTimer();
  }

  _scheduleDaynightTimer() {
    if (this._daynightTimer && typeof window !== "undefined" && typeof window.clearTimeout === "function") {
      window.clearTimeout(this._daynightTimer);
    }
    this._daynightTimer = null;
    if (this._themeMode !== "daynight") return;
    if (typeof window === "undefined" || typeof window.setTimeout !== "function") return;
    this._daynightTimer = window.setTimeout(() => {
      this._daynightTimer = null;
      this._applyThemeMode();
      this._render();
    }, msUntilNextDaynightBoundary());
  }

  _applyLocalPreferences() {
    if (this.classList && typeof this.classList.toggle === "function") {
      this.classList.toggle("reduced-motion", this._settingsPrefs.reduced_motion);
      this.classList.toggle("hide-hints", !this._settingsPrefs.show_hints);
      this.classList.toggle("large-interface-text", this._settingsPrefs.large_text);
      this.classList.toggle("rail-collapsed", this._settingsPrefs.rail_collapsed);
    }
    const toggle = this._shell && this._shell.sidebarToggle;
    if (toggle) {
      const collapsed = this._settingsPrefs.rail_collapsed === true;
      setAttr(toggle, "aria-label", collapsed ? "Развернуть боковое меню" : "Свернуть боковое меню");
      setAttr(toggle, "title", collapsed ? "Развернуть боковое меню" : "Свернуть боковое меню");
      toggle.innerHTML = "";
      toggle.appendChild(svgIcon(collapsed ? "chevron-right" : "chevron-left", "sidebar-collapse-icon"));
    }
  }

  _toggleRailCollapsed() {
    this._settingsPrefs.rail_collapsed = !this._settingsPrefs.rail_collapsed;
    this._persistUserPreferences();
    this._applyLocalPreferences();
  }

  async _loadUserPreferences() {
    if (this._preferencesLoaded || this._preferencesLoading || !this._hass?.connection?.sendMessagePromise) return;
    this._preferencesLoading = true;
    try {
      const response = await this._hass.connection.sendMessagePromise({
        type: "frontend/get_user_data",
        key: USER_PREFERENCES_KEY,
      });
      const saved = response && response.value;
      if (this._preferencesDirty || !saved || typeof saved !== "object") {
        this._preferencesLoaded = true;
        return;
      }
      if (THEME_MODES.includes(saved.theme_mode)) this._themeMode = saved.theme_mode;
      ["large_text", "reduced_motion", "show_hints", "rail_collapsed"].forEach((key) => {
        if (typeof saved[key] === "boolean") this._settingsPrefs[key] = saved[key];
      });
    } catch (error) {
    } finally {
      this._preferencesLoaded = true;
      this._preferencesLoading = false;
      this._applyThemeMode();
      this._render();
      this._flushUserPreferences();
    }
  }

  async _persistUserPreferences() {
    this._preferencesDirty = true;
    if (!this._preferencesLoaded && !this._preferencesLoading) {
      await this._loadUserPreferences();
    }
    await this._flushUserPreferences();
  }

  async _flushUserPreferences() {
    if (
      !this._preferencesLoaded
      || this._preferencesLoading
      || this._preferencesWriting
      || !this._preferencesDirty
      || !this._hass?.connection?.sendMessagePromise
    ) return;
    this._preferencesWriting = true;
    try {
      while (this._preferencesDirty) {
        this._preferencesDirty = false;
        const value = { theme_mode: this._themeMode, ...this._settingsPrefs };
        try {
          await this._hass.connection.sendMessagePromise({
            type: "frontend/set_user_data",
            key: USER_PREFERENCES_KEY,
            value,
          });
        } catch (error) {
          this._preferencesDirty = true;
          break;
        }
      }
    } finally {
      this._preferencesWriting = false;
    }
  }

  _cycleThemeMode() {
    const index = THEME_MODES.indexOf(this._themeMode);
    this._themeMode = THEME_MODES[(index + 1) % THEME_MODES.length];
    this._persistUserPreferences();
    this._applyThemeMode();
  }

  _updateThemeSwitcher() {
    const button = this._shell && this._shell.themeButton;
    if (!button) return;
    const meta = THEME_MODE_META[this._themeMode] || THEME_MODE_META.auto;
    setAttr(button, "title", meta.label);
    setAttr(button, "aria-label", meta.label);
    button.innerHTML = "";
    button.appendChild(svgIcon(meta.icon));
    button.appendChild(el("span", "theme-switch-hint", meta.hint));
  }

  async _load() {
    if (!this._hass || this._loadingPanel) return;
    this._loadingPanel = true;
    try {
      const results = await Promise.all([
        this._hass.callApi("GET", PANEL_API),
        this._hass.callApi("GET", MODE_API).catch(() => null),
        this._hass.callApi("GET", HOME_API).catch(() => null),
        this._hass.callApi("GET", WINDOWS_API).catch(() => null),
        this._hass.callApi("GET", SETUP_API).catch(() => null),
        this._hass.callApi("GET", IR_CODE_BINDINGS_API).catch(() => ({ bindings: [] })),
        this._hass.callApi("GET", DASHBOARD_API).catch(() => null),
        this._hass.callApi("GET", SCENARIOS_CATALOG_API).catch(() => null),
        this._hass.callApi("GET", PANEL_TABLET_PROFILE_API).catch(() => null),
        this._hass.callApi("GET", SCENARIOS_UPCOMING_API).catch(() => null),
        this._hass.callApi("GET", CLIMATE_RUNTIME_API).catch(() => null),
        this._hass.callApi("GET", CAPABILITIES_API).catch(() => null),
      ]);
      this._data = results[0];
      this._settings = {
        mode: results[1],
        home: results[2],
        windows: results[3],
        setup: results[4],
        irBindings: results[5],
      };
      this._homeDashboard = results[6];
      this._scenarios.catalog = results[7];
      if (typeof applyTabletProfile === "function") applyTabletProfile(this, results[8]);
      this._upcomingEvents = results[9];
      this._climateRuntime = results[10];
      this._deviceFeatures = await loadDeviceFeatureMatrix(this._hass, results[11]);
      const draftResumed = resumeFirstRunDraft(this);
      const wizardInProgress = draftResumed || (
        this._firstRun.completed !== true && this._firstRun.step !== "instructions"
      );
      const currentSetupRevision = this._settings.setup?.setup_revision;
      const setupRevisionChanged = wizardInProgress
        && Number.isSafeInteger(currentSetupRevision)
        && currentSetupRevision >= 0
        && Number.isSafeInteger(this._firstRun.setupRevision)
        && this._firstRun.setupRevision !== currentSetupRevision;
      if (
        wizardInProgress
        && Number.isSafeInteger(currentSetupRevision)
        && currentSetupRevision >= 0
      ) {
        this._firstRun.setupRevision = currentSetupRevision;
      }
      if (draftResumed || setupRevisionChanged) {
        await this._loadFirstRunOptions(setupRevisionChanged);
      }
      const recovered = this._error;
      this._error = false;
      this._loadedAt = Date.now();
      if (recovered || !this._technicalLog.length) {
        log(this, "success", "Связь с Hausman Hub установлена");
      }
    } catch (error) {
      this._error = true;
      log(this, "error", "Не удалось получить данные панели");
    }
    this._loadingPanel = false;
    this._render();
    if (this._energyMeter === null) loadEnergyMeter(this);
    loadDeviceDiscovery(this);
    if (this._activeSection === "energy" && this._energyHistory === null) {
      loadEnergyHistory(this);
    }
  }

  _refreshUpcomingCountdowns() {
    if (this._activeSection !== "overview") return;
    const root = this._shell && this._shell.summary;
    if (!root || typeof root.querySelectorAll !== "function") return;
    root.querySelectorAll("[data-upcoming-run-at]").forEach((node) => {
      const runAt = typeof node.getAttribute === "function" ? node.getAttribute("data-upcoming-run-at") : "";
      if (runAt) node.textContent = formatUpcomingCountdown(runAt, Date.now());
    });
  }

  async _loadScenarios() {
    if (!this._hass || this._scenarios.loading) return;
    this._scenarios.loading = true;
    try {
      const [list, catalog] = await Promise.all([
        this._hass.callApi("GET", SCENARIOS_API).catch(() => null),
        this._scenarios.catalog
          ? Promise.resolve(this._scenarios.catalog)
          : this._hass.callApi("GET", SCENARIOS_CATALOG_API).catch(() => null),
      ]);
      this._scenarios.list = list;
      this._scenarios.catalog = catalog;
      this._scenarios.error = false;
    } catch (error) {
      this._scenarios.error = true;
    } finally {
      this._scenarios.loading = false;
      if (this._activeSection === "scenarios") this._renderScenarios(this._shell.scenarios);
      if (this._activeSection === "settings" && this._activeSettingsView === "system") {
        this._renderSettings(this._shell.settings);
      }
    }
  }

  async _loadSettings() {
    if (!this._hass) return;
    let applied = false;
    try {
      const data = await this._hass.callApi("GET", CONNECTION_SETTINGS_API).catch(() => null);
      if (data && typeof data === "object" && !this._settingsDirty) {
        this._settingsData = {
          connection_mode: "home_assistant",
          smart_home_center_url: "",
          home_assistant_url: data.home_assistant_url || "",
        };
        this._settingsBaseline = { ...this._settingsData };
        applied = true;
      }
    } catch (error) {
    }
    if (applied && this._activeSection === "settings") this._renderSettings(this._shell.settings);
  }

  _screenUiState() {
    const ageSeconds = this._loadedAt ? Math.floor((Date.now() - this._loadedAt) / 1000) : null;
    if (this._error) return offlineUiState("screen", ageSeconds);
    if (!this._data) return loadingUiState("screen");
    if (ageSeconds !== null && ageSeconds * 1000 > STALE_MS) return staleUiState("screen", ageSeconds);
    return null;
  }

  async _post(path, payload, confirmText) {
    if (this._busy) return false;
    const uiState = this._screenUiState();
    if (!canExecuteCommand(uiState)) {
      this._notice = uiState.message;
      this._render();
      return false;
    }
    if (confirmText && !window.confirm(confirmText)) return false;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      const receipt = await this._hass.callApi("POST", path, withCorrelationId(path, payload));
      this._notice = this._receiptText(receipt);
      this._error = false;
      await this._load();
      return true;
    } catch (error) {
      const policy = resolveApiError(error);
      this._notice = policy.safeMessage;
      if (policy.retryPolicy === "after_refresh") await this._load();
      return false;
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _setClimateManual(roomId, deviceId, manual) {
    return setClimateManualMode(this, roomId, deviceId, manual);
  }

  async _save(section, path, payload, confirmText, successText) {
    if (this._busy) return;
    if (confirmText && !window.confirm(confirmText)) return;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      await this._hass.callApi("POST", path, payload);
      this._dirty[section] = false;
      this._notice = successText;
      this._error = false;
    } catch (error) {
      this._dirty[section] = false;
      this._notice = apiErrorMessage(error);
    } finally {
      this._busy = false;
    }
    await this._load();
  }

  _receiptText(receipt) {
    const statuses = {
      confirmed: "Применено и подтверждено наблюдением.",
      pending: "Команды отправлены, подтверждение ещё проверяется.",
      partial: "Применено частично.",
      unavailable: "Состояние климатического контура недоступно.",
      up_to_date: "Состояние уже соответствует сохранённому.",
      denied: "Действие отклонено защитой.",
      failed: "Действие не выполнено.",
    };
    const status = receipt && typeof receipt.status === "string" ? receipt.status : "";
    return statuses[status] || `Статус операции: ${status || "неизвестен"}.`;
  }

  _names(section, code) {
    const names = this._data && this._data.snapshot && this._data.snapshot.display_names;
    const group = names && names[section];
    const localGroup = LOCAL_DISPLAY_NAMES[section];
    const translated = (group && group[code]) || (localGroup && localGroup[code]);
    if (translated) return translated;
    if (section === "blocked_reasons") {
      return "Требуется дополнительная настройка";
    }
    return code;
  }

  _render() {
    if (!this.shadowRoot) return;
    if (this._firstRunDraftReady) persistFirstRunDraft(this);
    this._ensureShell();
    if (typeof syncIntercomQuickAccess === "function") syncIntercomQuickAccess(this);
    const shell = this._shell;
    shell.container.className = this._isFirstRunActive() || this._isFirstRunDeferred()
      ? "setup-shell" : "";
    applyFeedback(shell.notice, this._notice, setAttr);
    if (this._error) {
      shell.banner.style.display = "";
      const uiState = this._screenUiState();
      if (uiState) shell.banner.textContent = uiState.message;
      if (!this._data) {
        this._clearDynamic();
        return;
      }
    } else {
      shell.banner.style.display = "none";
    }
    if (!this._data) {
      shell.loading.style.display = "";
      this._clearDynamic();
      return;
    }
    shell.loading.style.display = "none";
    this._renderHeaderStatus(this._data.readiness);
    if (this._isFirstRunActive()) {
      this._activeSection = "overview";
      shell.brandSubtitle.textContent = "Первичная настройка дома";
      shell.nav.hidden = true;
      shell.sidebar.hidden = true;
      shell.wizard.hidden = false;
      PANEL_SECTIONS.forEach((section) => { shell.sectionNodes[section.id].hidden = true; });
      this._renderFirstRun(shell.wizard, this._settings.setup);
      return;
    }
    shell.wizard.hidden = true;
    shell.wizard.innerHTML = "";
    if (this._isFirstRunDeferred()) {
      shell.nav.hidden = true;
      shell.sidebar.hidden = true;
      this._activeSection = "overview";
      shell.brandSubtitle.textContent = SECTION_SUBTITLES.overview;
      this._renderHeaderStatus(this._data.readiness);
      this._renderReadiness(shell.readiness, this._data.readiness, this._data.snapshot, this._settings.setup);
      this._renderOverviewSummary(shell.summary, this._settings.setup, this._data.snapshot);
      this._renderRooms(shell.rooms, this._data.snapshot, this._settings.setup);
      PANEL_SECTIONS.forEach((section) => {
        shell.sectionNodes[section.id].hidden = section.id !== "overview";
      });
      return;
    }
    shell.nav.hidden = false;
    shell.sidebar.hidden = false;
    this._chooseInitialSection();
    this._renderReadiness(shell.readiness, this._data.readiness, this._data.snapshot, this._settings.setup);
    const snapshot = this._data.snapshot;
    this._renderOverviewSummary(shell.summary, this._settings.setup, snapshot);
    if (!this._dirty.wizard) {
      this._renderContour(shell.contour, snapshot, this._settings.setup);
    }
    if (this._activeSection === "climate" && this._activeClimateView === "overview") {
      this._renderHomeSection("climate", shell.climateOverview);
    }
    this._renderRooms(shell.rooms, snapshot, this._settings.setup);
    if (!this._dirty.profiles) this._renderProfiles(shell.profiles, this._settings.setup);
    if (!this._dirty.schedule) this._renderSchedule(shell.schedule, this._settings);
    if (!this._dirty.home) this._renderHome(shell.home, this._settings.home);
    if (!this._dirty.windows) this._renderWindows(shell.windows, this._settings.windows);
    if (this._activeSection === "climate" && this._activeClimateView === "assistant") {
      this._renderAssistant(shell.assistant);
    }
    if (this._activeSection === "scenarios") this._renderScenarios(shell.scenarios);
    if (this._activeSection === "settings" && !this._isEditingConnectionField()) {
      this._renderSettings(shell.settings);
    }
    if (shell.homeSections && shell.homeSections[this._activeSection]) {
      this._renderHomeSection(this._activeSection, shell.homeSections[this._activeSection]);
    }
    this._syncSectionVisibility();
  }

  _ensureShell() {
    if (this._shell) return;
    const root = this.shadowRoot;
    const stylesheet = el("link");
    stylesheet.rel = "stylesheet";
    const container = el("main");
    container.style.visibility = "hidden";
    const revealPanel = () => {
      container.style.visibility = "";
      if (this._styleRevealTimer && typeof window !== "undefined" && typeof window.clearTimeout === "function") {
        window.clearTimeout(this._styleRevealTimer);
      }
      this._styleRevealTimer = null;
    };
    stylesheet.addEventListener("load", revealPanel);
    stylesheet.addEventListener("error", revealPanel);
    stylesheet.href = PANEL_CSS_URL;
    root.appendChild(stylesheet);
    root.appendChild(container);
    if (typeof window !== "undefined" && typeof window.setTimeout === "function") {
      this._styleRevealTimer = window.setTimeout(revealPanel, 2000);
    }
    const header = el("header", "page-header");
    const brand = el("div", "page-brand");
    brand.appendChild(brandMark());
    const brandCopy = el("div", "brand-copy");
    brandCopy.appendChild(el("h1", "brand-title", "Hausman Hub"));
    const brandSubtitle = el("div", "subtitle", SECTION_SUBTITLES.overview);
    brandCopy.appendChild(brandSubtitle);
    brand.appendChild(brandCopy);
    header.appendChild(brand);
    const headerActions = el("div", "page-header-actions");
    const statusPill = el("div", "status-pill", "Загрузка состояния…");
    setAttr(statusPill, "role", "status");
    headerActions.appendChild(statusPill);
    const versionBadge = el("div", "version-badge muted", "");
    versionBadge.style.display = "none";
    headerActions.appendChild(versionBadge);
    const themeButton = el("button", "theme-switch");
    themeButton.type = "button";
    themeButton.addEventListener("click", () => this._cycleThemeMode());
    headerActions.appendChild(themeButton);
    const headerIntercom = el("button", "header-intercom");
    headerIntercom.type = "button";
    setAttr(headerIntercom, "aria-label", "Открыть домофон");
    headerIntercom.appendChild(svgIcon("intercom", "header-intercom-icon"));
    headerIntercom.appendChild(el("span", null, "Домофон"));
    headerIntercom.addEventListener("click", () => openIntercomFromRail(this));
    headerActions.appendChild(headerIntercom);
    const kioskButton = createKioskButton(this, "header-kiosk", { el, svgIcon });
    headerActions.appendChild(kioskButton);
    const refreshButton = el("button", "theme-switch header-refresh");
    refreshButton.type = "button";
    setAttr(refreshButton, "aria-label", "Обновить данные");
    refreshButton.appendChild(svgIcon("refresh", "header-refresh-icon"));
    refreshButton.appendChild(el("span", "header-refresh-label", "Обновить"));
    refreshButton.addEventListener("click", () => this._load());
    headerActions.appendChild(refreshButton);
    const headerClock = el("div", "header-clock");
    const headerClockDate = el("span", "header-clock-date");
    const headerClockTime = el("span", "header-clock-time");
    headerClock.appendChild(headerClockDate);
    headerClock.appendChild(headerClockTime);
    headerActions.appendChild(headerClock);
    header.appendChild(headerActions);
    container.appendChild(header);
    const banner = el("div", "banner", "Данные Hausman Hub недоступны. Проверьте интеграцию и повторите.");
    setAttr(banner, "role", "alert");
    banner.style.display = "none";
    container.appendChild(banner);
    const notice = el("div", "notice");
    setAttr(notice, "role", "status");
    setAttr(notice, "aria-live", "polite");
    notice.style.display = "none";
    container.appendChild(notice);
    const loading = el("div", "loading muted", "Загрузка данных Hausman Hub…");
    loading.style.display = "none";
    container.appendChild(loading);
    const wizard = el("section", "first-run-wizard");
    setAttr(wizard, "aria-label", "Мастер первичной настройки Hausman Hub");
    wizard.hidden = true;
    container.appendChild(wizard);
    const sidebar = el("aside", "app-sidebar");
    const sidebarBrand = el("div", "sidebar-brand");
    sidebarBrand.appendChild(brandMark());
    const sidebarBrandCopy = el("div", "sidebar-brand-copy");
    sidebarBrandCopy.appendChild(el("strong", null, "HAUSMAN"));
    sidebarBrandCopy.appendChild(el("small", null, "Управление домом"));
    sidebarBrand.appendChild(sidebarBrandCopy);
    sidebar.appendChild(sidebarBrand);
    const nav = el("nav", "tab-bar");
    setAttr(nav, "aria-label", "Разделы Hausman Hub");
    setAttr(nav, "role", "tablist");
    const tabs = {};
    PANEL_SECTIONS.forEach((section, index) => {
      const button = el("button", "tab");
      button.type = "button";
      button.id = `hausman-tab-${section.id}`;
      setAttr(button, "data-section", section.id);
      setAttr(button, "role", "tab");
      setAttr(button, "aria-label", section.label);
      setAttr(button, "aria-controls", `hausman-${section.id}`);
      button.addEventListener("click", () => this._activateSection(section.id));
      button.addEventListener("keydown", (event) => this._handleTabKey(event, index));
      button.appendChild(svgIcon(section.icon, "tab-icon"));
      button.appendChild(el("span", "tab-label", section.label));
      nav.appendChild(button);
      tabs[section.id] = button;
    });
    sidebar.appendChild(nav);
    const intercom = el("button", "sidebar-intercom");
    setAttr(intercom, "aria-label", "Открыть домофон");
    intercom.appendChild(svgIcon("intercom", "sidebar-intercom-icon"));
    intercom.appendChild(el("span", "sidebar-intercom-label", "Домофон"));
    intercom.appendChild(el("small", "sidebar-intercom-action", "Открыть"));
    intercom.addEventListener("click", () => openIntercomFromRail(this));
    sidebar.appendChild(intercom);
    const sidebarKiosk = createKioskButton(this, "sidebar-kiosk", { el, svgIcon });
    sidebar.appendChild(sidebarKiosk);
    const sidebarFooter = el("div", "sidebar-footer");
    const sidebarVersion = el("span", "sidebar-version", "Версия —");
    sidebarFooter.appendChild(sidebarVersion);
    const sidebarToggle = el("button", "sidebar-collapse");
    sidebarToggle.type = "button";
    setAttr(sidebarToggle, "aria-label", "Свернуть боковое меню");
    sidebarToggle.appendChild(svgIcon("chevron-left", "sidebar-collapse-icon"));
    sidebarToggle.addEventListener("click", () => this._toggleRailCollapsed());
    sidebarFooter.appendChild(sidebarToggle);
    sidebar.appendChild(sidebarFooter);
    container.appendChild(sidebar);
    const kioskDock = createKioskDock(this, { el, svgIcon, setAttr });
    container.appendChild(kioskDock);
    const kioskSurface = el("section", "kiosk-panorama");
    container.appendChild(kioskSurface);
    const sectionNodes = {};
    PANEL_SECTIONS.forEach((section) => {
      const node = el("section");
      node.id = `hausman-${section.id}`;
      setAttr(node, "role", "tabpanel");
      setAttr(node, "aria-label", section.label);
      setAttr(node, "aria-labelledby", `hausman-tab-${section.id}`);
      container.appendChild(node);
      sectionNodes[section.id] = node;
    });
    const readiness = el("div", "overview-tablet-readiness");
    const summary = el("div", "overview-tablet-content");
    const rooms = el("div", "overview-tablet-legacy-rooms");
    sectionNodes.overview.appendChild(readiness);
    sectionNodes.overview.appendChild(summary);
    sectionNodes.overview.appendChild(rooms);
    const climate = sectionNodes.climate;
    const climateNav = el("nav", "climate-subnav");
    setAttr(climateNav, "aria-label", "Экраны климата");
    setAttr(climateNav, "role", "tablist");
    const climateTabs = {};
    const climateViews = {};
    CLIMATE_VIEWS.forEach((view, index) => {
      const button = el("button", "climate-subtab", view.label);
      button.type = "button";
      button.id = `hausman-climate-tab-${view.id}`;
      setAttr(button, "role", "tab");
      setAttr(button, "aria-controls", `hausman-climate-${view.id}`);
      button.addEventListener("click", () => this._activateClimateView(view.id));
      button.addEventListener("keydown", (event) => this._handleClimateTabKey(event, index));
      climateNav.appendChild(button);
      climateTabs[view.id] = button;
      const node = el("div", "climate-view");
      node.id = `hausman-climate-${view.id}`;
      setAttr(node, "role", "tabpanel");
      setAttr(node, "aria-labelledby", button.id);
      climateViews[view.id] = node;
    });
    climate.appendChild(climateNav);
    CLIMATE_VIEWS.forEach((view) => climate.appendChild(climateViews[view.id]));
    const { overview: climateOverview, contour, profiles, schedule, home, windows, assistant } = climateViews;
    const scenarios = el("div");
    sectionNodes.scenarios.appendChild(scenarios);
    const settings = el("div");
    sectionNodes.settings.appendChild(settings);
    const homeSections = {};
    ["lighting", "rooms", "media", "security", "devices", "energy"].forEach((sectionId) => {
      const content = el("div", "home-section-content");
      sectionNodes[sectionId].appendChild(content);
      homeSections[sectionId] = content;
    });
    this._shell = {
      container,
      banner, notice, loading, brandSubtitle, statusPill, versionBadge, themeButton, tabs, nav, sidebar, sidebarVersion, sidebarToggle, sectionNodes, wizard,
      headerClockDate, headerClockTime,
      readiness, summary, rooms,
      climateNav, climateTabs, climateViews, climateOverview, contour, profiles, schedule, home, windows, assistant,
      scenarios, settings,
      homeSections,
      kioskButton, sidebarKiosk, kioskDock, kioskSurface,
      renderKiosk: () => renderKiosk(this, kioskSurface, { el, svgIcon, setAttr, showIntercom: typeof isIntercomQuickAccessVisible === "function" && isIntercomQuickAccessVisible(this), exit: () => kioskButton.click(), openIntercom: () => openIntercomFromRail(this) }),
    };
    this._updateThemeSwitcher();
    this._updateHeaderClock();
  }

  _updateHeaderClock() {
    const shell = this._shell;
    if (!shell || !shell.headerClockDate) return;
    const now = new Date();
    shell.headerClockDate.textContent = HEADER_CLOCK_DATE_FORMAT.format(now);
    shell.headerClockTime.textContent = HEADER_CLOCK_TIME_FORMAT.format(now);
  }

  _clearDynamic() {
    ["readiness", "summary", "rooms"].forEach((name) => {
      this._shell[name].innerHTML = "";
    });
    if (!this._dirty.wizard) this._shell.contour.innerHTML = "";
    ["profiles", "schedule", "home", "windows", "assistant"].forEach((name) => {
      if (!this._dirty[name]) this._shell[name].innerHTML = "";
    });
  }

  _chooseInitialSection() {
    if (this._activeSection) return;
    const setup = this._settings.setup;
    this._activeSection = setup && setup.status === "not_configured" ? "climate" : "overview";
  }

  _activateSection(section, focus = false) {
    if (!PANEL_SECTIONS.some((item) => item.id === section)) return;
    const changed = this._activeSection !== section;
    if (changed) {
      this._notice = "";
      this._climateOverlay = null;
      this._mediaOverlay = null;
      if (typeof this._activeDeviceModalClose === "function") this._activeDeviceModalClose();
    }
    this._activeSection = section;
    this._syncSectionVisibility();
    if (section === "climate") {
      this._syncClimateVisibility();
      if (this._activeClimateView === "overview") {
        this._renderHomeSection("climate", this._shell.climateOverview);
      }
      if (this._activeClimateView === "assistant") {
        this._renderAssistant(this._shell.assistant);
        if (!this._assistant.loaded) this._loadAssistant();
      }
    }
    if (section === "scenarios") {
      this._renderScenarios(this._shell.scenarios);
      if (!this._scenarios.list && !this._scenarios.loading) this._loadScenarios();
    }
    if (section === "settings") {
      this._renderSettings(this._shell.settings);
      if (!this._settingsDirty && !this._settingsBaseline.home_assistant_url) this._loadSettings();
    }
    if (section === "energy") {
      loadEnergyHistory(this);
      loadEnergyMeter(this);
    }
    if (this._shell.homeSections && this._shell.homeSections[section]) {
      this._renderHomeSection(section, this._shell.homeSections[section]);
    }
    if (changed) writeNavigationRoute(this);
    if (focus) focusNode(this._shell && this._shell.tabs[section]);
  }

  _activateClimateView(viewId, focus = false) {
    if (!CLIMATE_VIEWS.some((view) => view.id === viewId)) return;
    const changed = this._activeClimateView !== viewId;
    this._activeClimateView = viewId;
    this._syncClimateVisibility();
    if (viewId === "overview") {
      this._renderHomeSection("climate", this._shell.climateOverview);
    }
    if (viewId === "assistant") {
      this._renderAssistant(this._shell.assistant);
      if (!this._assistant.loaded) this._loadAssistant();
    }
    if (changed && this._activeSection === "climate") writeNavigationRoute(this);
    if (focus) focusNode(this._shell && this._shell.climateTabs[viewId]);
  }

  _handleClimateTabKey(event, index) {
    const key = event && event.key;
    let next = null;
    if (key === "ArrowRight") next = (index + 1) % CLIMATE_VIEWS.length;
    if (key === "ArrowLeft") next = (index - 1 + CLIMATE_VIEWS.length) % CLIMATE_VIEWS.length;
    if (key === "Home") next = 0;
    if (key === "End") next = CLIMATE_VIEWS.length - 1;
    if (next === null) return;
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    this._activateClimateView(CLIMATE_VIEWS[next].id, true);
  }

  _handleTabKey(event, index) {
    const key = event && event.key;
    let next = null;
    if (key === "ArrowRight" || key === "ArrowDown") next = (index + 1) % PANEL_SECTIONS.length;
    if (key === "ArrowLeft" || key === "ArrowUp") next = (index - 1 + PANEL_SECTIONS.length) % PANEL_SECTIONS.length;
    if (key === "Home") next = 0;
    if (key === "End") next = PANEL_SECTIONS.length - 1;
    if (next === null) return;
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    this._activateSection(PANEL_SECTIONS[next].id, true);
  }

  _syncSectionVisibility() {
    if (!this._shell) return;
    if (this._kioskMode) this._shell.renderKiosk();
    this.classList?.toggle?.("overview-active", !this._kioskMode && this._activeSection === "overview");
    const climateView = CLIMATE_VIEWS.find((view) => view.id === this._activeClimateView);
    this._shell.brandSubtitle.textContent = this._activeSection === "climate" && climateView
      ? climateView.subtitle
      : (SECTION_SUBTITLES[this._activeSection] || "Настройка Hausman Hub");
    const climateDirty = this._dirty.wizard || this._dirty.profiles || this._dirty.schedule || this._dirty.home || this._dirty.windows || this._dirty.assistant;
    const dirtyBySection = {
      climate: climateDirty,
      settings: this._settingsDirty,
    };
    PANEL_SECTIONS.forEach((section) => {
      const active = !this._kioskMode && section.id === this._activeSection;
      this._shell.sectionNodes[section.id].hidden = !active;
      const tab = this._shell.tabs[section.id];
      setAttr(tab, "aria-current", active ? "page" : "false");
      setAttr(tab, "aria-selected", active ? "true" : "false");
      setAttr(tab, "tabindex", active ? "0" : "-1");
      const dirty = dirtyBySection[section.id];
      tab.className = `tab${dirty ? " is-dirty" : ""}`;
      tab.title = dirty ? "Есть несохранённые изменения" : "";
    });
    updateDeviceDiscoveryBadge(this, { el, setAttr });
    this._syncClimateVisibility();
  }

  _syncClimateVisibility() {
    if (!this._shell || !this._shell.climateViews) return;
    const activeView = CLIMATE_VIEWS.find((view) => view.id === this._activeClimateView);
    if (this._activeSection === "climate" && activeView) {
      this._shell.brandSubtitle.textContent = activeView.subtitle;
    }
    CLIMATE_VIEWS.forEach((view) => {
      const active = view.id === this._activeClimateView;
      this._shell.climateViews[view.id].hidden = !active;
      const tab = this._shell.climateTabs[view.id];
      setAttr(tab, "aria-selected", active ? "true" : "false");
      setAttr(tab, "tabindex", active ? "0" : "-1");
      tab.className = `climate-subtab${active ? " is-active" : ""}`;
    });
  }

  _renderHeaderStatus(readiness) {
    const status = readiness && readiness.status;
    this._shell.statusPill.textContent = READINESS_LABELS[status] || "Состояние уточняется";
    setAttr(this._shell.statusPill, "data-status", status || "unknown");
    const version = this._data && this._data.integration_version;
    this._shell.versionBadge.textContent = version ? `Версия ${version}` : "";
    this._shell.versionBadge.style.display = version ? "" : "none";
    if (this._shell.sidebarVersion) this._shell.sidebarVersion.textContent = version ? `Версия ${version}` : "Версия —";
  }

  _renderOverviewSummary(container, setup, snapshot) {
    if (this._homeDashboard) {
      renderOverviewContent(this, container, {
        el, svgIcon, setAttr,
        renderEnergyOverviewCard: (panel, target) => renderEnergyOverviewCard(panel, target, { el, svgIcon, setAttr }),
        runApi: SCENARIOS_RUN_API,
        upcomingCancelApi: SCENARIOS_UPCOMING_CANCEL_API,
      });
      return;
    }
    container.innerHTML = "";
    if (!setup && !snapshot) return;
    renderOverviewNavigationSummary(this, container, this._overviewMetrics(snapshot, setup), {
      el, setAttr, svgIcon, sections: PANEL_SECTIONS,
    });
    renderEnergyOverviewCard(this, container, { el, setAttr, svgIcon });
  }

  _overviewMetrics(snapshot, setup = null) {
    return renderHomeSection.overviewMetrics(
      snapshot, setup, (value) => this._temp(value), (value) => this._humidity(value)
    );
  }

  _markDirty(section, indicator = null) {
    this._dirty[section] = true;
    if (indicator) indicator.hidden = false;
    this._syncSectionVisibility();
  }

  _bridgeModeName(code) {
    const labels = {
      managed: "Управляемый",
      native: "Нативное управление",
      disabled: "Выключен",
      shadow: "Наблюдение",
      canary: "Ограниченное управление",
    };
    return labels[code] || "Неизвестен";
  }

  _roomModeName(code) {
    const translated = this._names("room_modes", code);
    if (translated && translated !== code) return translated;
    const labels = {
      automatic: "Автоматический",
      observe: "Наблюдение",
      manual: "Ручной",
      disabled: "Выключен",
    };
    return labels[code] || "Нет данных";
  }

  _profileName(code) {
    const translated = this._names("profiles", code);
    if (translated && translated !== code) return translated;
    const labels = {
      day: "Дневной",
      night: "Ночной",
      away: "Никого нет дома",
      comfort: "Комфорт",
      eco: "Экономичный",
    };
    return labels[code] || "Индивидуальный";
  }

  _contourStatusName(code) {
    const translated = this._names("contour_statuses", code);
    if (translated && translated !== code) return translated;
    const labels = {
      normal: "Норма",
      ready: "Готов",
      active: "Работает",
      disabled: "Выключен",
      warning: "Нужно внимание",
      unavailable: "Недоступен",
    };
    return labels[code] || "Состояние уточняется";
  }

  _contourModeName(code) {
    const translated = this._names("contour_modes", code);
    if (translated && translated !== code) return translated;
    return this._roomModeName(code);
  }

  _roomName(roomId) {
    if (!roomId) return "";
    const setupRooms = this._settings && this._settings.setup && this._settings.setup.rooms || [];
    const snapshotRooms = this._data && this._data.snapshot && this._data.snapshot.rooms || [];
    const room = [...setupRooms, ...snapshotRooms].find((item) => item && item.id === roomId);
    if (room && room.name) return room.name;
    const fallbacks = {
      living: "Гостиная", living_room: "Гостиная", bedroom: "Спальня",
      kitchen: "Кухня", bathroom: "Ванная", kids: "Детская", kids_room: "Детская",
    };
    return fallbacks[roomId] || "Комната";
  }

  _dataStatusName(code) {
    const translated = this._names("data_statuses", code);
    if (translated && translated !== code) return translated;
    const labels = {
      current: "Свежие данные",
      stale: "Данные устарели",
      unavailable: "Нет данных",
      missing: "Нет данных",
      suspect: "Данные требуют проверки",
    };
    return labels[code] || "Нет данных";
  }

  _assistantFields(settings) {
    const source = settings || {};
    return {
      enabled: Boolean(source.enabled),
      preset: source.preset || "custom",
      base_url: source.base_url || "",
      model: source.model || "",
      api_key: "",
      clear_key: false,
    };
  }

  async _loadAssistant(reload = false) {
    if (!this._hass || this._assistant.loading || (this._assistant.loaded && !reload)) return;
    this._assistant.loading = true;
    this._assistant.error = false;
    this._render();
    try {
      const data = await this._hass.callApi("GET", AI_ASSISTANT_API);
      this._assistant.data = data || null;
      this._assistant.fields = this._assistantFields(data && data.settings);
      this._assistant.loaded = true;
      this._dirty.assistant = false;
    } catch (error) {
      this._assistant.error = true;
      this._assistant.loaded = true;
    } finally {
      this._assistant.loading = false;
      this._render();
    }
  }

  _assistantStatusName(code) {
    const labels = {
      ready: "Готово",
      provider_unavailable: "Поставщик недоступен",
      provider_timeout: "Таймаут",
      provider_error: "Ошибка поставщика",
      provider_output_invalid: "Некорректный ответ поставщика",
      disabled: "Выключен",
      unconfigured: "Не настроен",
    };
    return labels[code] || "Статус неизвестен";
  }

  _assistantErrorName(code) {
    const labels = {
      auth: "Ошибка авторизации",
      http: "Ошибка HTTP",
      timeout: "Таймаут",
      invalid: "Некорректный ответ",
    };
    return labels[code] || this._assistantStatusName(code);
  }

  _assistantRecommendationName(code) {
    const labels = {
      use_deterministic_evidence: "Используйте проверенные данные",
      review_temperature_gap: "Проверьте расхождение температур",
      refresh_evidence: "Обновите данные",
      verify_physical_feedback: "Проверьте физическую обратную связь",
      verify_window_state: "Проверьте состояние окна",
      inspect_state_mismatch: "Проверьте расхождение состояния",
    };
    return labels[code] || "Рекомендация недоступна";
  }

  _assistantRiskName(code) {
    const labels = {
      provider_unavailable: "Поставщик недоступен",
      provider_timeout: "Таймаут поставщика",
      provider_error: "Ошибка поставщика",
      provider_output_invalid: "Некорректный ответ поставщика",
      temperature_outside_comfort_band: "Превышение комфортного диапазона",
      stale_state: "Данные устарели",
      physical_feedback_unconfirmed: "Физическая обратная связь не подтверждена",
      window_not_confirmed_closed: "Окно не подтверждено закрытым",
      state_mismatch: "Расхождение состояния",
    };
    return labels[code] || "Риск не определён";
  }

  _assistantDate(value) {
    const timestamp = Number(value);
    return Number.isFinite(timestamp) && timestamp > 0
      ? new Date(timestamp).toLocaleString("ru-RU")
      : "Время неизвестно";
  }

  async _saveAssistant() {
    const fields = this._assistant.fields;
    if (!fields || this._busy) return;
    const baseUrl = fields.base_url.trim();
    const model = fields.model.trim();
    if (!baseUrl || !model) {
      this._notice = "Введите адрес API и модель.";
      this._render();
      return;
    }
    const payload = {
      enabled: fields.enabled,
      preset: fields.preset,
      base_url: baseUrl,
      model,
    };
    if (fields.api_key.trim()) payload.api_key = fields.api_key.trim();
    else if (fields.clear_key) payload.clear_key = true;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      const result = await this._hass.callApi("POST", AI_ASSISTANT_SETTINGS_API, payload);
      this._assistant.data = {
        ...(this._assistant.data || {}),
        settings: result && result.settings ? result.settings : { ...fields, key_set: !fields.clear_key },
      };
      this._assistant.fields = this._assistantFields(this._assistant.data.settings);
      this._dirty.assistant = false;
      this._notice = "Настройки помощника сохранены.";
    } catch (error) {
      this._notice = apiErrorMessage(error);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _refreshAssistant() {
    if (this._busy || !this._assistant.loaded) return;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      const result = await this._hass.callApi("POST", AI_ASSISTANT_REFRESH_API, {});
      if (result && result.advisory) {
        this._assistant.data = { ...this._assistant.data, last_advisory: result.advisory };
      }
      try {
        const data = await this._hass.callApi("GET", AI_ASSISTANT_API);
        if (data) {
          this._assistant.data = data;
          if (!this._dirty.assistant) this._assistant.fields = this._assistantFields(data.settings);
        }
      } catch {}
      this._notice = "Совет обновлён.";
    } catch (error) {
      this._notice = apiErrorMessage(error);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _renderAssistant(container) {
    if (!container) return;
    container.innerHTML = "";
    container.className = "assistant-screen";
    const heading = el("div", "section-heading");
    heading.appendChild(el("h2", null, "Умный помощник"));
    heading.appendChild(el("p", "section-intro", "Настройте поставщика и получайте советы по климату."));
    container.appendChild(heading);
    if (this._assistant.loading) {
      container.appendChild(el("div", "card assistant-card muted", "Загрузка настроек помощника…"));
      return;
    }
    if (this._assistant.error || !this._assistant.loaded || !this._assistant.data) {
      const card = el("div", "card assistant-card empty-state");
      card.appendChild(el("p", null, "Настройки помощника недоступны."));
      const retry = el("button", "secondary", "Повторить");
      retry.type = "button";
      retry.disabled = this._busy;
      retry.addEventListener("click", () => this._loadAssistant(true));
      card.appendChild(retry);
      container.appendChild(card);
      return;
    }
    const fields = this._assistant.fields || this._assistantFields(this._assistant.data.settings);
    this._assistant.fields = fields;
    const settings = this._assistant.data.settings || {};
    const connection = el("div", "card assistant-card assistant-connection");
    connection.appendChild(el("h3", null, "Подключение"));
    const enabledLabel = el("label", "checkbox-field assistant-enabled");
    const enabled = el("input");
    enabled.type = "checkbox";
    enabled.checked = fields.enabled;
    enabled.addEventListener("change", () => {
      fields.enabled = enabled.checked;
      this._markDirty("assistant");
    });
    enabledLabel.appendChild(enabled);
    enabledLabel.appendChild(el("span", null, "Включить помощник"));
    connection.appendChild(enabledLabel);
    const formGrid = el("div", "assistant-form-grid");
    let baseUrl;
    let model;
    const presetLabel = el("label", "assistant-field");
    presetLabel.appendChild(el("span", "assistant-field-label", "Поставщик сервиса"));
    const preset = selectField([
      { value: "deepseek", label: "DeepSeek" },
      { value: "openai", label: "Совместимый с OpenAI" },
      { value: "custom", label: "Свой поставщик" },
    ], fields.preset, () => {
      fields.preset = preset.value;
      this._markDirty("assistant");
    });
    presetLabel.appendChild(preset);
    formGrid.appendChild(presetLabel);
    const baseLabel = el("label", "assistant-field");
    baseLabel.appendChild(el("span", "assistant-field-label", "Базовый URL"));
    baseUrl = el("input");
    baseUrl.type = "text";
    baseUrl.value = fields.base_url;
    baseUrl.addEventListener("input", () => {
      fields.base_url = baseUrl.value;
      this._markDirty("assistant");
    });
    baseLabel.appendChild(baseUrl);
    formGrid.appendChild(baseLabel);
    const modelLabel = el("label", "assistant-field");
    modelLabel.appendChild(el("span", "assistant-field-label", "Модель"));
    model = el("input");
    model.type = "text";
    model.value = fields.model;
    model.addEventListener("input", () => {
      fields.model = model.value;
      this._markDirty("assistant");
    });
    modelLabel.appendChild(model);
    formGrid.appendChild(modelLabel);
    const keyLabel = el("label", "assistant-field");
    keyLabel.appendChild(el("span", "assistant-field-label", "Новый ключ API"));
    const apiKey = el("input");
    apiKey.type = "password";
    apiKey.value = "";
    apiKey.addEventListener("input", () => {
      fields.api_key = apiKey.value;
      if (apiKey.value) fields.clear_key = false;
      this._markDirty("assistant");
    });
    keyLabel.appendChild(apiKey);
    formGrid.appendChild(keyLabel);
    connection.appendChild(formGrid);
    connection.appendChild(el(
      "p",
      "muted assistant-key-status",
      fields.clear_key ? "Ключ будет удалён после сохранения." : (settings.key_set ? "Ключ сохранён" : "Ключ не задан")
    ));
    const actions = el("div", "assistant-actions");
    if (settings.key_set || fields.clear_key) {
      const clear = el("button", "secondary", fields.clear_key ? "Не удалять ключ" : "Удалить сохранённый ключ");
      clear.type = "button";
      clear.disabled = this._busy;
      clear.addEventListener("click", () => {
        fields.clear_key = !fields.clear_key;
        fields.api_key = "";
        this._markDirty("assistant");
        this._render();
      });
      actions.appendChild(clear);
    }
    const save = el("button", null, "Сохранить настройки");
    save.type = "button";
    save.disabled = this._busy;
    save.addEventListener("click", () => this._saveAssistant());
    actions.appendChild(save);
    connection.appendChild(actions);
    container.appendChild(connection);
    this._renderAssistantStats(container, this._assistant.data.stats || {});
    this._renderAssistantAdvisory(container, this._assistant.data.last_advisory);
  }

  _renderAssistantStats(container, stats) {
    const card = el("div", "card assistant-card");
    card.appendChild(el("h3", null, "Статистика вызовов"));
    const aggregates = Array.isArray(stats.aggregates) ? stats.aggregates : [];
    const total = aggregates.reduce((sum, item) => ({
      calls: sum.calls + Number(item.calls || 0),
      successes: sum.successes + Number(item.successes || 0),
      prompt: sum.prompt + Number(item.prompt_tokens || 0),
      completion: sum.completion + Number(item.completion_tokens || 0),
      latency: sum.latency + Number(item.latency_ms || 0),
    }), { calls: 0, successes: 0, prompt: 0, completion: 0, latency: 0 });
    const grid = el("div", "assistant-grid");
    [
      ["Вызовы", this._assistantNumber(total.calls)],
      ["Успешно", this._assistantNumber(total.successes)],
      ["Prompt tokens", this._assistantNumber(total.prompt)],
      ["Completion tokens", this._assistantNumber(total.completion)],
      ["Средняя задержка", total.calls ? `${Math.round(total.latency / total.calls)} мс` : "Нет данных"],
    ].forEach(([label, value]) => {
      const item = el("div", "assistant-stat");
      item.appendChild(el("span", "muted", label));
      item.appendChild(el("strong", null, value));
      grid.appendChild(item);
    });
    card.appendChild(grid);
    const calls = Array.isArray(stats.recent_calls) ? stats.recent_calls : [];
    card.appendChild(el("h4", "assistant-subheading", "Последние вызовы"));
    if (!calls.length) {
      card.appendChild(el("p", "muted", "Вызовов пока нет."));
    } else {
      const list = el("div", "assistant-list");
      calls.slice(-10).reverse().forEach((call) => {
        const row = el("div", "assistant-call");
        const details = el("div");
        const timestamp = el("time", null, this._assistantDate(call.ts));
        details.appendChild(timestamp);
        details.appendChild(el("div", "muted", `${call.preset || "Поставщик"}: ${call.model || "Модель"}`));
        row.appendChild(details);
        const failed = Boolean(call.error_class);
        row.appendChild(el(
          "span",
          `status-badge assistant-call-status ${failed ? "is-attention" : "is-ready"}`,
          failed ? this._assistantErrorName(call.error_class) : this._assistantStatusName(call.status)
        ));
        list.appendChild(row);
      });
      card.appendChild(list);
    }
    container.appendChild(card);
  }

  _renderAssistantAdvisory(container, advisory) {
    const card = el("div", "card assistant-card assistant-advisory");
    card.appendChild(el("h3", null, "Последний совет"));
    if (!advisory) {
      card.appendChild(el("p", "muted", "Советов пока нет."));
    } else {
      card.appendChild(el("span", "status-badge is-ready", this._assistantStatusName(advisory.status)));
      card.appendChild(el("p", "muted", `Сформирован: ${this._assistantDate(advisory.generated_at)}`));
      const recommendations = Array.isArray(advisory.recommendations) ? advisory.recommendations : [];
      const risks = Array.isArray(advisory.risk_flags) ? advisory.risk_flags : [];
      card.appendChild(el("h4", "assistant-subheading", "Рекомендации"));
      if (recommendations.length) {
        const list = el("ul", "advisory-list is-recommendation");
        recommendations.forEach((item) => {
          const room = item.room_id ? ` · ${this._roomName(item.room_id)}` : "";
          list.appendChild(el("li", null, `${this._assistantRecommendationName(item.code)}${room}`));
        });
        card.appendChild(list);
      } else {
        card.appendChild(el("p", "muted", "Рекомендаций нет."));
      }
      card.appendChild(el("h4", "assistant-subheading", "Риски"));
      if (risks.length) {
        const list = el("ul", "advisory-list is-risk");
        risks.forEach((item) => {
          const room = item.room_id ? ` · ${this._roomName(item.room_id)}` : "";
          list.appendChild(el("li", null, `${this._assistantRiskName(item.code)}${room}`));
        });
        card.appendChild(list);
      } else {
        card.appendChild(el("p", "muted", "Рисков нет."));
      }
    }
    const refresh = el("button", "secondary", "Обновить совет");
    refresh.type = "button";
    refresh.disabled = this._busy;
    refresh.addEventListener("click", () => this._refreshAssistant());
    const actions = el("div", "assistant-actions");
    actions.appendChild(refresh);
    card.appendChild(actions);
    container.appendChild(card);
  }

  _assistantNumber(value) {
    return new Intl.NumberFormat("ru-RU").format(Number(value || 0));
  }

  _renderReadiness(container, readiness, snapshot, setup = null) {
    if (this._homeDashboard) {
      const renderKey = overviewHeroRenderKey(this, readiness);
      const hasHero = (container.childNodes?.length || container.children?.length || 0) > 0;
      if (hasHero && this._overviewHeroRenderKey === renderKey) return;
      renderOverviewHero(this, container, readiness, {
        el, svgIcon, setAttr,
        openRoom: (room) => openRoomFromOverview(this, room),
        enterKiosk: () => this._shell?.kioskButton?.click?.(),
        refresh: () => this._load(),
      });
      this._overviewHeroRenderKey = renderKey;
      return;
    }
    container.innerHTML = "";
    const metrics = this._overviewMetrics(snapshot, setup);
    const readinessStatus = readiness && readiness.status || "not_ready";
    const ready = readinessStatus === "ready";
    const card = el("div", "card hero overview-hero");
    const head = el("div", "overview-hero-head");
    const icon = el("span", "overview-hero-icon");
    icon.appendChild(svgIcon("home"));
    head.appendChild(icon);
    const copy = el("div", "overview-hero-copy");
    copy.appendChild(el(
      "div",
      "hero-status",
      ready ? "Дом в комфортном режиме" : (READINESS_LABELS[readinessStatus] || "Состояние уточняется")
    ));
    copy.appendChild(el(
      "p",
      "section-intro",
      ready ? "Все основные показатели находятся в заданных диапазонах" : "Проверьте состояние и завершите необходимую настройку"
    ));
    head.appendChild(copy);
    head.appendChild(el("span", "status-badge overview-mode-status", this._bridgeModeName(readiness.bridge_mode)));
    card.appendChild(head);
    const metricGrid = el("div", "overview-hero-metrics");
    const deviceMetric = metrics.runtimeAvailable
      ? [metrics.activeDevices, "Устройств активно"]
      : [metrics.deviceCount, "Устройств настроено"];
    const roomMetric = metrics.runtimeAvailable
      ? [metrics.roomCount, "Комнаты"]
      : [metrics.roomCount, "Комнат настроено"];
    [
      [metrics.temperature, "Температура"],
      [metrics.humidity, "Влажность"],
      deviceMetric,
      roomMetric,
    ].forEach(([value, label]) => {
      const metric = el("div", "overview-hero-metric");
      metric.appendChild(el("strong", null, value));
      metric.appendChild(el("span", "muted", label));
      metricGrid.appendChild(metric);
    });
    card.appendChild(metricGrid);
    if (!metrics.runtimeAvailable && metrics.roomCount > 0) {
      card.appendChild(el(
        "div",
        "empty-state muted",
        `Конфигурация сохранена: ${metrics.roomCount} ${this._roomCountWord(metrics.roomCount)} · `
          + `${metrics.deviceCount} ${this._deviceCountWord(metrics.deviceCount)}. `
          + "Текущие показатели появятся после включения наблюдения или управления."
      ));
    }
    if (Array.isArray(readiness.reasons) && readiness.reasons.length) {
      const reasons = el("div", "reasons");
      readiness.reasons.forEach((reason) => {
        reasons.appendChild(el("span", "chip", this._names("blocked_reasons", reason)));
      });
      card.appendChild(reasons);
    }
    const modeSettings = this._settings.mode;
    if (modeSettings) {
      const switchRow = el("div", "overview-hero-actions");
      const managed = modeSettings.mode === "managed";
      const rollout = modeSettings.rollout || {};
      const button = el(
        "button",
        managed ? "secondary" : null,
        managed ? "Остановить управление" : "Запустить пилотную комнату"
      );
      button.disabled = this._busy || (!managed && rollout.enable_allowed !== true);
      button.addEventListener("click", () => {
        const target = managed ? "disabled" : "managed";
        this._save(
          "mode",
          MODE_API,
          { mode: target, expected_mode: modeSettings.mode, confirm: managed ? null : true },
          managed
            ? "Выключить управление климатом? Устройства больше не будут получать команды от Hausman Hub."
            : "Запустить пилотную комнату? Старый контур не должен одновременно управлять её устройствами.",
          managed ? "Управление климатом остановлено." : "Пилотная комната запущена."
        );
      });
      switchRow.appendChild(button);
      card.appendChild(switchRow);
      if (!managed) {
        card.appendChild(renderRolloutReadiness(snapshot, setup, rollout, { el }));
      }
    }
    container.appendChild(card);
  }

  _renderRooms(container, snapshot, setup = null) {
    if (this._homeDashboard) {
      container.innerHTML = "";
      return;
    }
    container.innerHTML = "";
    if (!snapshot) {
      const card = el(
        "div",
        "card empty-state muted",
        "Данные комнат появятся после настройки и запуска климатического контура."
      );
      container.appendChild(card);
      return;
    }
    const heading = el("div", "overview-section-heading overview-rooms-heading");
    const headingCopy = el("div");
    headingCopy.appendChild(el("h2", null, "Комнаты"));
    headingCopy.appendChild(el("p", "section-intro", "Короткая сводка по каждому пространству"));
    heading.appendChild(headingCopy);
    container.appendChild(heading);
    const grid = el("div", "cards overview-room-grid");
    (snapshot.rooms || []).forEach((room) => {
      const card = el("button", "card overview-room-card");
      card.type = "button";
      setAttr(card, "aria-label", `Открыть комнату ${room.name}`);
      card.addEventListener("click", () => openRoomFromOverview(this, room));
      const cardHead = el("div", "overview-room-head");
      const roomCopy = el("div");
      roomCopy.appendChild(el("h3", null, room.name));
      const activeProfile = room.active_profile || (room.targets && room.targets.profile);
      roomCopy.appendChild(el("p", "muted", activeProfile
        ? `${this._profileName(activeProfile)} профиль`
        : this._roomModeName(room.mode)));
      cardHead.appendChild(roomCopy);
      const dataStatus = this._dataStatusName(room.actual && room.actual.data_status);
      cardHead.appendChild(el(
        "span",
        `status-badge ${dataStatus === "Свежие данные" ? "is-ready" : "is-attention"}`,
        dataStatus === "Свежие данные" ? "В норме" : dataStatus
      ));
      card.appendChild(cardHead);
      const devices = room.devices || [];
      const activeDevices = devices.filter((device) => (
        !["off", "idle", "unavailable", "unknown"].includes(device.state)
      )).length;
      const metrics = el("div", "overview-room-metrics");
      [
        ["Температура", this._temp(room.temperature)],
        ["Влажность", this._humidity(room.humidity)],
        ["Активно", `${activeDevices} ${activeDevices === 1 ? "устройство" : "устройства"}`],
        ["Воздух", dataStatus === "Свежие данные" ? "Хорошо" : "Проверить"],
      ].forEach(([label, value]) => {
        const metric = el("div");
        metric.appendChild(el("span", "assistant-field-label", label));
        metric.appendChild(el("strong", null, value));
        metrics.appendChild(metric);
      });
      card.appendChild(metrics);
      grid.appendChild(card);
    });
    if (!(snapshot.rooms || []).length) {
      const setupSummary = setup && setup.summary || {};
      const configuredRoomCount = Number(setupSummary.room_count)
        || (setup && Array.isArray(setup.rooms) ? setup.rooms.length : 0);
      const message = configuredRoomCount > 0
        ? `${configuredRoomCount} ${this._roomCountWord(configuredRoomCount)} настроено. `
          + "Текущие показатели появятся после включения наблюдения или управления."
        : "Комнаты пока не добавлены.";
      grid.appendChild(el("div", "card empty-state muted", message));
    }
    container.appendChild(grid);
  }

  _isFirstRunActive() {
    const setup = this._settings.setup;
    const postSaveStep = this._firstRun.contourSaved && [
      "code_source", "tablet", "completion",
    ].includes(this._firstRun.step);
    return Boolean(
      (
        setup && setup.status === "not_configured"
        || postSaveStep
      )
      && !this._firstRun.deferred && !this._firstRun.completed
    );
  }

  _isFirstRunDeferred() {
    const setup = this._settings.setup;
    return Boolean(
      setup && setup.status === "not_configured"
      && this._firstRun.deferred && !this._firstRun.completed
    );
  }

  _firstRunRoomCandidates(roomId) {
    const candidates = (this._firstRun.options && this._firstRun.options.devices || []).filter((candidate) => (
      candidate.room_id === roomId
      || (candidate.can_add === true
        && candidate.room_id === "" && candidate.suggested_room_id === roomId)
    ));
    return this._firstRunDistinctCandidates(candidates);
  }

  _firstRunDuplicateKey(candidate) {
    const types = (candidate.suggested_types || []).filter((type) => ACTIVE_DEVICE_TYPES.has(type)).sort();
    const identity = [
      candidate.room_id || candidate.suggested_room_id || "",
      normalizedText(candidate.name || ""),
      normalizedText(candidate.device_name || candidate.name || ""),
      normalizedText(candidate.manufacturer || ""),
      normalizedText(candidate.model || ""),
    ];
    return types.length && identity.every(Boolean) ? [...identity, types.join(",")].join("|") : null;
  }

  _firstRunDistinctCandidates(candidates) {
    const availableIdentities = new Set();
    candidates.forEach((candidate) => {
      if (!["available", "already_configured"].includes(candidate.status)) return;
      const identity = this._firstRunDuplicateKey(candidate);
      if (identity) availableIdentities.add(identity);
    });
    return candidates.filter((candidate) => {
      if (candidate.status !== "unavailable") return true;
      const identity = this._firstRunDuplicateKey(candidate);
      return !identity || !availableIdentities.has(identity);
    });
  }

  _firstRunRoomlessCandidates() {
    return (this._firstRun.options && this._firstRun.options.devices || []).filter((candidate) => (
      candidate.can_add === true
      && candidate.room_id === ""
      && !candidate.suggested_room_id
    ));
  }

  _firstRunAreaCandidates() {
    return (this._firstRun.options && this._firstRun.options.devices || []).filter(
      (candidate) => candidate.configured === true
        || ["available", "unavailable"].includes(candidate.status)
    );
  }

  _firstRunPhysicalGroups(candidates) {
    const groups = new Map();
    this._firstRunDistinctCandidates(candidates).forEach((candidate) => {
      const id = candidate.device_group_id || `candidate:${candidate.candidate_id}`;
      if (!groups.has(id)) groups.set(id, []);
      groups.get(id).push(candidate);
    });
    return Array.from(groups.values());
  }

  _firstRunPhysicalGroupId(group) {
    const first = group && group[0];
    return first ? (first.device_group_id || `candidate:${first.candidate_id}`) : "";
  }

  _firstRunIsYandexVirtual(candidate) {
    return normalizedText(candidate && candidate.manufacturer) === "yandex"
      && normalizedText(candidate && candidate.model) === "yndx-0006";
  }

  _firstRunPublicIdentity(candidate) {
    const key = String(candidate && (candidate.candidate_key || candidate.candidate_id) || "");
    const compact = key.replace(/[^a-z0-9]/gi, "").slice(-4).toUpperCase();
    return compact || "—";
  }

  _firstRunGroupAvailable(group) {
    return (group || []).some((candidate) => candidate.status === "available"
      || candidate.status === "already_configured");
  }

  _firstRunGroupRoom(group) {
    const groupId = this._firstRunPhysicalGroupId(group);
    if (Object.prototype.hasOwnProperty.call(this._firstRun.areaAssignments, groupId)) {
      return this._firstRun.areaAssignments[groupId];
    }
    return (group && group[0] && group[0].room_id) || "";
  }

  _assignFirstRunGroup(group, roomId) {
    const groupId = this._firstRunPhysicalGroupId(group);
    if (!groupId) return;
    const originalRoomId = (group && group[0] && group[0].room_id) || "";
    if (roomId === originalRoomId) delete this._firstRun.areaAssignments[groupId];
    else this._firstRun.areaAssignments[groupId] = roomId;
    this._firstRun.areaSaveError = "";
    this._firstRun.areaSaveStatus = "";
  }

  _firstRunCandidateSelectable(candidate, room) {
    return candidate.can_add === true && candidate.room_id === room.id;
  }

  _firstRunRoomNameRoot(name) {
    let root = normalizedText(name || "").replace(/ё/g, "е").replace(/[^а-я]/g, "");
    const endings = ["нная", "ная", "яя", "ая", "ий", "ый", "ое", "ее", "ь", "а", "я"];
    let stripped = true;
    while (stripped) {
      stripped = false;
      for (const ending of endings) {
        if (root.endsWith(ending) && root.length > ending.length) {
          root = root.slice(0, -ending.length);
          stripped = true;
          break;
        }
      }
    }
    return root;
  }

  _firstRunCandidateRoomName(candidate) {
    const rooms = (this._firstRun.options && this._firstRun.options.rooms) || [];
    const room = rooms.find((item) => item.id === candidate.room_id);
    return (room && room.name) || candidate.suggested_room_name || candidate.room_id || "Без комнаты";
  }

  _firstRunPossibleRoomCandidates(room) {
    const roomName = normalizedText(room.name || room.id).replace(/ё/g, "е");
    const root = this._firstRunRoomNameRoot(room.name || room.id);
    return (this._firstRun.options && this._firstRun.options.devices || []).filter((candidate) => (
      candidate.room_id && candidate.room_id !== room.id
      && (candidate.suggested_types || []).some((type) => ACTIVE_DEVICE_TYPES.has(type))
      && (() => {
        const candidateName = normalizedText([candidate.name, candidate.device_name]
          .filter(Boolean).join(" ")).replace(/ё/g, "е");
        return (roomName && candidateName.includes(roomName))
          || (root.length >= 4 && candidateName.includes(root));
      })()
    ));
  }

  _firstRunCandidateStatusName(candidate) {
    const names = ((this._firstRun.options || {}).display_names || {}).device_status || {};
    return names[candidate.status] || candidate.status || "Статус не указан";
  }

  _firstRunCandidateReasonName(candidate) {
    const names = ((this._firstRun.options || {}).display_names || {}).suggestion_reasons || {};
    return names[candidate.reason] || candidate.reason || "Причина не указана";
  }

  _firstRunCandidateHint(candidate, room) {
    if (candidate.status === "unavailable") {
      return "Устройство сейчас недоступно в Home Assistant. Попробуйте обновить список.";
    }
    if (candidate.room_id && candidate.room_id !== room.id) {
      return `Сейчас относится к зоне «${this._firstRunCandidateRoomName(candidate)}». Если это неверно, переназначьте область в Home Assistant.`;
    }
    if (!candidate.room_id) {
      return "Сначала назначьте устройству комнату на первом шаге и сохраните привязку в Home Assistant.";
    }
    return "Это устройство сейчас нельзя добавить в комнату.";
  }

  _firstRunRoomChoices(state, candidates) {
    const choices = [];
    candidates.forEach((candidate, candidateIndex) => {
      const suggestedTypes = Array.isArray(candidate.suggested_types) ? candidate.suggested_types : [];
      if (!suggestedTypes.length) {
        choices.push({
          candidate,
          device: { channel: null, selected: false },
          key: `${candidate.candidate_key || candidate.candidate_id}:unknown`,
          order: candidateIndex * 10,
          pseudo: true,
          type: null,
        });
        return;
      }
      suggestedTypes.forEach((type, typeIndex) => {
        const key = `${candidate.candidate_key || candidate.candidate_id}:${type}`;
        const device = state.devices[key];
        if (!device) return;
        choices.push({ candidate, device, key, order: candidateIndex * 10 + typeIndex, type });
      });
    });
    return this._firstRunDistinctPurposeChoices(choices);
  }

  _firstRunDistinctPurposeChoices(choices) {
    const grouped = new Map();
    choices.forEach((choice) => {
      const physicalId = choice.candidate.device_group_id;
      const key = physicalId
        ? `${physicalId}:${choice.type || "unknown"}`
        : `candidate:${choice.candidate.candidate_id}:${choice.type || "unknown"}`;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(choice);
    });
    const result = [];
    grouped.forEach((alternatives) => {
      alternatives.sort((left, right) => {
        const score = (choice) => (
          Number(choice.device.selected === true) * 10000
          + Number(choice.candidate.configured === true) * 2000
          + Number(choice.candidate.status === "already_configured") * 1000
          + Number(choice.candidate.status === "available") * 500
          + Number(choice.candidate.status === "unavailable") * 200
          + Number(choice.candidate.recommended_type === choice.type) * 50
        );
        return score(right) - score(left)
          || String(left.candidate.name || "").length - String(right.candidate.name || "").length
          || String(left.candidate.candidate_id).localeCompare(
            String(right.candidate.candidate_id), "ru"
          );
      });
      const selected = alternatives[0];
      alternatives.slice(1).forEach((duplicate) => {
        duplicate.device.selected = false;
        duplicate.device.channel = null;
      });
      result.push(selected);
    });
    return result.sort((left, right) => left.order - right.order);
  }

  _firstRunDeviceGroups(choiceList, room, fields, allChoices, searchable) {
    return renderFirstRunDeviceGroups(this, choiceList, room, fields, allChoices, searchable, {
      ACTIVE_DEVICE_TYPES, CONTROL_CHANNEL_LABELS, ZIGBEE2MQTT_IMAGE_PATTERN,
      el, normalizedText, selectField, setAttr, svgIcon,
    });
  }

  async _testFirstRunControlChannel(choice) {
    const plan = resolveControlChannelTest(this, choice);
    if (!plan.ready || !this._hass) {
      return {
        status: "failed",
        title: "Канал не проверен",
        detail: plan.reason || "Нет безопасной команды для проверки.",
      };
    }
    try {
      const probe = await this._hass.callApi("POST", DEVICE_ACTIONS_API, {
        targetId: plan.targetId,
        actionId: plan.actionId,
        value: plan.probeValue,
      });
      const restored = await this._hass.callApi("POST", DEVICE_ACTIONS_API, {
        targetId: plan.targetId,
        actionId: plan.actionId,
        value: plan.value,
      });
      return summarizeControlChannelReceipts(probe, restored);
    } catch (error) {
      try {
        await this._hass.callApi("POST", DEVICE_ACTIONS_API, {
          targetId: plan.targetId,
          actionId: plan.actionId,
          value: plan.value,
        });
      } catch {
      }
      return {
        status: "failed",
        title: "Проверка не выполнена",
        detail: "Не удалось завершить тест. Проверьте текущее заданное значение устройства перед повтором.",
      };
    }
  }

  _firstRunClimateSources(room, fields, choices) {
    return renderFirstRunClimateSources(this, room, fields, choices, {
      ZIGBEE2MQTT_IMAGE_PATTERN, el, setAttr, svgIcon,
    });
  }

  _collapsibleDeviceSection(title, hint, contentNode, expanded) {
    const section = el("div", "collapsible-section");
    const toggle = el("button", "collapsible-toggle secondary");
    toggle.type = "button";
    const chevron = svgIcon("chevron", "collapsible-chevron");
    toggle.appendChild(chevron);
    toggle.appendChild(el("span", null, title));
    setAttr(toggle, "aria-expanded", expanded ? "true" : "false");
    const body = el("div", "collapsible-body");
    if (hint) body.appendChild(el("div", "muted", hint));
    body.appendChild(contentNode);
    body.hidden = !expanded;
    toggle.addEventListener("click", () => {
      const show = body.hidden;
      body.hidden = !show;
      setAttr(toggle, "aria-expanded", show ? "true" : "false");
    });
    section.appendChild(toggle);
    section.appendChild(body);
    return section;
  }

  _firstRunRoomState(room) {
    let state = this._firstRun.rooms[room.id];
    const newState = !state;
    if (!state) {
      state = {
        day: { humidity: 53, strategy: "normal", temperature: 25 },
        devices: {},
        included: false,
        maxTemperature: 27,
        minTemperature: 24.5,
        night: { humidity: 50, strategy: "normal", temperature: 25.5 },
        report: null,
        showAllDevices: false,
      };
      this._firstRun.rooms[room.id] = state;
    }
    (this._firstRun.options && this._firstRun.options.devices || []).forEach((candidate) => {
      const suggestedTypes = Array.isArray(candidate.suggested_types) ? candidate.suggested_types : [];
      suggestedTypes.forEach((type) => {
        const candidateKey = candidate.candidate_key || candidate.candidate_id;
        const key = `${candidateKey}:${type}`;
        if (state.devices[key]) {
          state.devices[key].candidateId = candidate.candidate_id;
          state.devices[key].candidateKey = candidateKey;
          return;
        }
        state.devices[key] = {
          candidateId: candidate.candidate_id,
          candidateKey,
          channel: null,
          selected: false,
          type,
        };
      });
    });
    if (newState) state.included = Object.values(state.devices).some((device) => device.selected);
    return state;
  }

  _firstRunInvalidate(roomId = null) {
    this._firstRun.conflict = false;
    this._firstRun.draft = null;
    this._firstRun.issues = [];
    this._firstRun.validation = null;
    if (roomId) this._firstRun.validRooms.delete(roomId);
    if (this._firstRunDraftReady) persistFirstRunDraft(this);
  }

  _firstRunPayload(roomIds) {
    const options = this._firstRun.options;
    if (!options) return { error: "Комнаты и устройства ещё загружаются." };
    const setupRevision = this._firstRun.setupRevision;
    if (!Number.isSafeInteger(setupRevision) || setupRevision < 0) {
      return { error: "Настройки изменились. Обновите мастер и повторите действие." };
    }
    const selectedIds = roomIds || Array.from(this._firstRun.validRooms);
    if (!selectedIds.length) return { error: "Сначала успешно проверьте хотя бы одну комнату." };
    const channels = new Set(options.control_channels || []);
    const candidateIds = new Map();
    (options.devices || []).forEach((candidate) => {
      const candidateKey = candidate.candidate_key || candidate.candidate_id;
      (Array.isArray(candidate.suggested_types) ? candidate.suggested_types : []).forEach((type) => {
        candidateIds.set(`${candidateKey}:${type}`, candidate.candidate_id);
      });
    });
    const rooms = [];
    const selectedCandidates = new Set();
    for (const roomId of selectedIds) {
      const room = (options.rooms || []).find((item) => item.id === roomId);
      const state = room && this._firstRunRoomState(room);
      if (!room || !state || !state.included) continue;
      const profiles = ["day", "night"].map((profile) => {
        const values = state[profile];
        const temperature = Number(values.temperature);
        const humidity = Number(values.humidity);
        if (
          values.temperature === "" || !Number.isFinite(temperature)
          || temperature < 18 || temperature > 28 || !Number.isInteger(temperature * 2)
        ) return { error: `Проверьте температуру профиля «${profile === "day" ? "День" : "Ночь"}».` };
        if (
          values.humidity === "" || !Number.isFinite(humidity)
          || humidity < 30 || humidity > 70 || !Number.isInteger(humidity)
        ) return { error: `Проверьте влажность профиля «${profile === "day" ? "День" : "Ночь"}».` };
        if (!STRATEGY_ORDER.includes(values.strategy)) {
          return { error: `Выберите стратегию профиля «${profile === "day" ? "День" : "Ночь"}».` };
        }
        return {
          humidity: Math.round(humidity),
          strategy: values.strategy,
          temperature,
        };
      });
      const invalidProfile = profiles.find((profile) => profile.error);
      if (invalidProfile) return { error: invalidProfile.error, roomId };
      const minTemperature = state.minTemperature === "" || state.minTemperature === null
        ? null : Number(state.minTemperature);
      const maxTemperature = state.maxTemperature === "" || state.maxTemperature === null
        ? null : Number(state.maxTemperature);
      if (
        (minTemperature !== null && (!Number.isFinite(minTemperature) || minTemperature < 18
          || minTemperature > 28 || !Number.isInteger(minTemperature * 2)))
        || (maxTemperature !== null && (!Number.isFinite(maxTemperature) || maxTemperature < 18
          || maxTemperature > 28 || !Number.isInteger(maxTemperature * 2)))
      ) {
        return { error: "Границы температуры допустимы от 18 до 28 °C с шагом 0,5.", roomId };
      }
      if (minTemperature !== null && maxTemperature !== null && minTemperature > maxTemperature) {
        return { error: "Минимальная температура не может быть выше максимальной.", roomId };
      }
      if (
        (minTemperature !== null && (minTemperature > profiles[0].temperature
          || minTemperature > profiles[1].temperature))
        || (maxTemperature !== null && (maxTemperature < profiles[0].temperature
          || maxTemperature < profiles[1].temperature))
      ) {
        return { error: "Границы температуры должны включать цели дневного и ночного профилей.", roomId };
      }
      const selectedRoomDevices = Object.values(state.devices).filter((device) => device.selected);
      const temperatureSources = selectedRoomDevices.filter((device) => device.type === "temperature_sensor");
      const humiditySources = selectedRoomDevices.filter((device) => device.type === "humidity_sensor");
      if (temperatureSources.length !== 1) {
        return {
          error: temperatureSources.length
            ? "Оставьте один главный датчик температуры комнаты. Контур не может принимать решения по нескольким источникам одновременно."
            : "Выберите главный датчик температуры комнаты. Без него климатический контур не сможет определить фактическую температуру.",
          roomId,
        };
      }
      if (humiditySources.length !== 1) {
        return {
          error: humiditySources.length
            ? "Оставьте один главный датчик влажности комнаты. Контур не может принимать решения по нескольким источникам одновременно."
            : "Выберите главный датчик влажности комнаты. Без него климатический контур не сможет определить фактическую влажность.",
          roomId,
        };
      }
      const devices = [];
      for (const device of Object.values(state.devices)) {
        if (!device.selected) continue;
        const candidateId = candidateIds.get(`${device.candidateKey || device.candidateId}:${device.type}`);
        if (!candidateId) {
          return { error: "Список устройств изменился. Обновите список и повторите проверку.", roomId };
        }
        if (selectedCandidates.has(candidateId)) {
          return { error: "Одно устройство нельзя выбрать для нескольких комнат или типов.", roomId };
        }
        selectedCandidates.add(candidateId);
        const item = { candidate_id: candidateId, type: device.type };
        if (ACTIVE_DEVICE_TYPES.has(device.type)) {
          if (device.channel && !channels.has(device.channel)) {
            return { error: "Выберите канал управления из доступного списка.", roomId };
          }
          item.control_channel = device.channel || null;
        }
        devices.push(item);
      }
      if (!devices.length) return { error: "Выберите хотя бы одно устройство комнаты.", roomId };
      rooms.push({
        room_id: room.id,
        target_temperature: profiles[0].temperature,
        target_humidity: profiles[0].humidity,
        strategy: profiles[0].strategy,
        min_temperature: minTemperature,
        max_temperature: maxTemperature,
        devices,
      });
    }
    if (!rooms.length) return { error: "Сначала успешно проверьте хотя бы одну комнату." };
    return {
      payload: {
        snapshot_revision: options.snapshot_revision,
        setup_revision: setupRevision,
        name: "Климат",
        mode: "automatic",
        rooms,
      },
    };
  }

  _firstRunProfiles() {
    return Array.from(this._firstRun.validRooms).map((roomId) => {
      const state = this._firstRun.rooms[roomId];
      return {
        profiles: {
          day: {
            strategy: state.day.strategy,
            target_humidity: Math.round(Number(state.day.humidity)),
            target_temperature: Number(state.day.temperature),
          },
          night: {
            strategy: state.night.strategy,
            target_humidity: Math.round(Number(state.night.humidity)),
            target_temperature: Number(state.night.temperature),
          },
        },
        room_id: roomId,
      };
    });
  }

  _firstRunIrDevices() {
    const setup = this._settings.setup || {};
    const bindings = new Map((this._settings.irBindings?.bindings || [])
      .filter((binding) => typeof binding?.candidate_id === "string"
        && typeof binding.configured_device_id === "string"
        && typeof binding.remote_entity_id === "string")
      .map((binding) => [binding.candidate_id, binding]));
    return (setup.rooms || []).flatMap((room) => (room.devices || []).flatMap((device) => {
      const binding = bindings.get(device.candidate_id);
      if (
        device.control_channel !== "universal_ir"
        || !binding
      ) return [];
      return [{
        deviceId: binding.configured_device_id,
        name: device.name || binding.configured_device_id,
        remoteEntityId: binding.remote_entity_id,
        roomId: room.id,
        type: device.type,
        profiles: room.profiles || {},
      }];
    }));
  }

  _firstRunActiveIrDevice() {
    const ir = this._firstRun.ir;
    const devices = this._firstRunIrDevices();
    if (!devices.some((device) => device.deviceId === ir.activeDeviceId)) {
      ir.activeDeviceId = devices.length ? devices[0].deviceId : null;
    }
    return devices.find((device) => device.deviceId === ir.activeDeviceId) || null;
  }

  _firstRunIrManualCommands(device) {
    if (device.type === "humidifier") {
      return [
        { commandName: "humidifier.on", label: "Включить" },
        { commandName: "humidifier.off", label: "Выключить" },
      ];
    }
    const temperatures = [device.profiles.day, device.profiles.night]
      .map((profile) => Number(profile && profile.target_temperature))
      .filter((temperature) => Number.isFinite(temperature));
    const commands = [{ commandName: "ac.off", label: "Выключить" }];
    Array.from(new Set(temperatures)).forEach((temperature) => {
      const label = `Охлаждение · ${temperature.toFixed(1)} °C`;
      commands.push({
        commandName: `ac.cool.${temperature.toFixed(1).replace(".", "_")}`,
        label,
      });
    });
    return commands;
  }

  async _loadFirstRunIrData() {
    const ir = this._firstRun.ir;
    if (ir.loading) return;
    ir.loading = true;
    ir.error = "";
    this._render();
    try {
      const [codes, scan] = await Promise.all([
        this._hass.callApi("GET", IR_CODES_API),
        this._hass.callApi("GET", IR_CODES_SCAN_API),
      ]);
      ir.codes = Array.isArray(codes && codes.codes) ? codes.codes : [];
      ir.scan = scan || {};
    } catch (error) {
      ir.error = apiErrorMessage(error);
    } finally {
      ir.loading = false;
      this._render();
    }
  }

  async _refreshFirstRunIrCodes() {
    const codes = await this._hass.callApi("GET", IR_CODES_API);
    this._firstRun.ir.codes = Array.isArray(codes && codes.codes) ? codes.codes : [];
  }

  async _importFirstRunIrCode(device, command, source) {
    const ir = this._firstRun.ir;
    if (ir.loading) return;
    ir.loading = true;
    ir.error = "";
    this._render();
    try {
      await this._hass.callApi("POST", IR_CODES_API, {
        device_id: device.deviceId,
        remote_entity_id: device.remoteEntityId,
        command_name: command.command_name,
        code_data: command.code_data,
        source,
      });
      await this._refreshFirstRunIrCodes();
    } catch (error) {
      ir.error = apiErrorMessage(error);
    } finally {
      ir.loading = false;
      this._render();
    }
  }

  async _testFirstRunIrCode(device, command) {
    const ir = this._firstRun.ir;
    if (ir.loading) return;
    ir.loading = true;
    ir.error = "";
    this._render();
    try {
      await this._hass.callApi("POST", IR_CODES_TEST_API, {
        device_id: device.deviceId,
        remote_entity_id: device.remoteEntityId,
        code_data: command.code_data,
      });
      this._notice = "Тестовая отправка ИК-кода выполнена.";
    } catch (error) {
      ir.error = apiErrorMessage(error);
    } finally {
      ir.loading = false;
      this._render();
    }
  }

  async _testFirstRunImportedIrCode(code) {
    const ir = this._firstRun.ir;
    if (ir.loading) return;
    ir.loading = true;
    ir.error = "";
    this._render();
    try {
      await this._hass.callApi("POST", IR_CODES_TEST_API, { code_id: code.code_id });
      this._notice = "Тестовая отправка ИК-кода выполнена.";
    } catch (error) {
      ir.error = apiErrorMessage(error);
    } finally {
      ir.loading = false;
      this._render();
    }
  }

  async _deleteFirstRunIrCode(code) {
    const ir = this._firstRun.ir;
    if (ir.loading) return;
    ir.loading = true;
    ir.error = "";
    this._render();
    try {
      await this._hass.callApi("POST", IR_CODES_DELETE_API, { code_id: code.code_id });
      await this._refreshFirstRunIrCodes();
    } catch (error) {
      ir.error = apiErrorMessage(error);
    } finally {
      ir.loading = false;
      this._render();
    }
  }

  async _learnFirstRunIrCode(device) {
    const ir = this._firstRun.ir;
    const commands = this._firstRunIrManualCommands(device);
    const manual = ir.manual;
    if (manual.deviceId !== device.deviceId) {
      manual.deviceId = device.deviceId;
      manual.index = 0;
      manual.statuses = {};
    }
    const command = commands[manual.index];
    if (!command || ir.loading) return;
    ir.loading = true;
    ir.error = "";
    manual.statuses[command.commandName] = "learning";
    this._render();
    try {
      await this._hass.callApi("POST", IR_CODES_LEARN_API, {
        device_id: device.deviceId,
        remote_entity_id: device.remoteEntityId,
        command_name: command.commandName,
      });
      manual.statuses[command.commandName] = "ready";
      manual.index += 1;
      await this._refreshFirstRunIrCodes();
    } catch (error) {
      manual.statuses[command.commandName] = error && error.status === 408 ? "timeout" : "error";
      if (manual.statuses[command.commandName] === "error") {
        ir.error = apiErrorMessage(error);
      }
    } finally {
      ir.loading = false;
      this._render();
    }
  }

  async _startFirstRun() {
    const setup = this._settings.setup;
    if (!setup) return;
    this._firstRun.setupRevision = setup.setup_revision;
    this._firstRun.step = "rooms";
    await this._loadFirstRunOptions();
  }

  async _loadFirstRunOptions(force = false) {
    if (this._firstRun.loading || (!force && this._firstRun.options)) return;
    const previousValidation = captureRoomValidation(this);
    this._firstRun.loading = true;
    this._firstRun.optionsError = false;
    this._render();
    try {
      this._firstRun.options = await this._hass.callApi("GET", DRAFT_API);
      const currentGroupIds = new Set(this._firstRunPhysicalGroups(
        this._firstRunAreaCandidates()
      ).map((group) => this._firstRunPhysicalGroupId(group)));
      Object.keys(this._firstRun.areaAssignments).forEach((groupId) => {
        if (!currentGroupIds.has(groupId)) delete this._firstRun.areaAssignments[groupId];
      });
      Object.keys(this._firstRun.rooms).forEach((roomId) => {
        const room = (this._firstRun.options.rooms || []).find((item) => item.id === roomId);
        if (room) this._firstRunRoomState(room);
      });
      if (!this._firstRunDraftReady) restoreFirstRunDraft(this);
      if (force) {
        reconcileRoomValidation(this, previousValidation);
        this._firstRun.draft = null;
        this._firstRun.issues = [];
        this._firstRun.validation = null;
      }
    } catch (error) {
      this._firstRun.optionsError = true;
    } finally {
      this._firstRun.loading = false;
    }
    this._render();
  }

  _openFirstRunAreaCreator(targetGroupId = null) {
    return openFirstRunAreaCreator(this, targetGroupId);
  }

  _closeFirstRunAreaCreator() {
    return closeFirstRunAreaCreator(this);
  }

  async _createFirstRunArea(rawName) {
    return createFirstRunArea(this, rawName, normalizedText);
  }

  async _saveFirstRunAreaAssignments() {
    if (this._busy || !this._firstRun.options) return;
    const assignments = [];
    const groups = this._firstRunPhysicalGroups(this._firstRunAreaCandidates());
    groups.forEach((group) => {
      const groupId = this._firstRunPhysicalGroupId(group);
      if (!Object.prototype.hasOwnProperty.call(this._firstRun.areaAssignments, groupId)) return;
      const roomId = this._firstRun.areaAssignments[groupId];
      assignments.push({
        candidate_ids: group.map((candidate) => candidate.candidate_id),
        room_id: roomId,
      });
    });
    if (!assignments.length) return;
    this._busy = true;
    this._firstRun.areaSaveError = "";
    this._firstRun.areaSaveStatus = "Сохраняю привязки в Home Assistant…";
    this._render();
    try {
      const receipt = await this._hass.callApi("POST", AREA_ASSIGNMENTS_API, {
        snapshot_revision: this._firstRun.options.snapshot_revision,
        assignments,
      });
      const deviceCount = Number(receipt && receipt.updated_devices) || 0;
      const entityCount = Number(receipt && receipt.updated_entities) || 0;
      this._firstRun.areaAssignments = {};
      this._firstRun.areaSaveStatus = `Сохранено в Home Assistant: устройств — ${deviceCount}, отдельных сущностей — ${entityCount}.`;
      this._notice = "Привязки комнат сохранены в Home Assistant.";
      await this._loadFirstRunOptions(true);
    } catch (error) {
      this._firstRun.areaSaveStatus = "";
      this._firstRun.areaSaveError = `${apiErrorMessage(error)} Выбор не потерян; проверьте области устройств в Home Assistant и повторите попытку.`;
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _deferFirstRun() {
    this._firstRun.deferred = true;
    this._activeSection = "overview";
    this._render();
  }

  _openFirstRunRoom(roomId) {
    this._firstRun.roomId = roomId;
    this._firstRun.step = "room";
    this._activeRoomSetupPane = "devices";
    this._render();
  }

  _firstRunBackToRooms() {
    this._firstRun.roomId = null;
    this._firstRun.step = "rooms";
    this._render();
  }

  async _checkFirstRunRoom(roomId) {
    const roomState = this._firstRun.rooms[roomId];
    if (roomState) roomState.included = true;
    const collected = this._firstRunPayload([roomId]);
    if (collected.error) {
      roomState.report = { issues: [{ message: collected.error }], save_allowed: false, status: "blocked" };
      this._firstRun.validRooms.delete(roomId);
      this._render();
      return;
    }
    this._busy = true;
    this._render();
    try {
      const draft = await this._hass.callApi("POST", DRAFT_API, collected.payload);
      const validation = await this._hass.callApi("POST", DRAFT_VALIDATE_API, draft);
      const state = this._firstRun.rooms[roomId];
      state.report = validation;
      if (validation.status === "ready" && validation.save_allowed === true) {
        this._firstRun.validRooms.add(roomId);
      } else {
        this._firstRun.validRooms.delete(roomId);
      }
    } catch (error) {
      this._firstRun.rooms[roomId].report = {
        issues: [{ message: apiErrorMessage(error) }],
        save_allowed: false,
        status: "blocked",
      };
      this._firstRun.validRooms.delete(roomId);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _saveFirstRunHome() {
    const home = this._firstRun.home;
    const high = Number(home.heating_lockout_high);
    const low = Number(home.heating_lockout_low);
    const heatingOn = Number(home.central_heating_temperature_on ?? 35);
    const heatingOff = Number(home.central_heating_temperature_off ?? 30);
    const airConditionerMinimum = Number(
      home.air_conditioner_minimum_outdoor_temperature ?? -5
    );
    if (
      home.heating_lockout_high === "" || home.heating_lockout_low === ""
      || !Number.isFinite(high) || !Number.isFinite(low) || high < -40 || high > 60
      || low < -40 || low > 60 || low >= high
      || home.central_heating_temperature_on === ""
      || home.central_heating_temperature_off === ""
      || !Number.isFinite(heatingOn) || !Number.isFinite(heatingOff)
      || heatingOn < -40 || heatingOn > 120 || heatingOff < -40 || heatingOff > 120
      || heatingOff >= heatingOn
      || home.air_conditioner_minimum_outdoor_temperature === ""
      || !Number.isFinite(airConditionerMinimum)
      || airConditionerMinimum < -40 || airConditionerMinimum > 60
    ) {
      this._firstRun.homeError = "Проверьте пороги: нижний должен быть строго меньше верхнего.";
      this._render();
      return;
    }
    this._busy = true;
    this._firstRun.homeError = "";
    this._render();
    try {
      this._firstRun.setupRevision = (await this._hass.callApi("POST", HOME_API, {
        central_heating_entity_id: home.central_heating_entity_id || null,
        heating_lockout_high: high,
        heating_lockout_low: low,
        central_heating_temperature_on: heatingOn,
        central_heating_temperature_off: heatingOff,
        air_conditioner_minimum_outdoor_temperature: airConditionerMinimum,
        ...homeEnvironmentSourcePayload(home),
        presence_entity_id: home.presence_entity_id || null,
      })).setup_revision;
      this._firstRun.step = "validation";
    } catch (error) {
      this._firstRun.homeError = homeEnvironmentSaveError(error);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _validateFirstRun() {
    const collected = this._firstRunPayload();
    if (collected.error) {
      this._firstRun.issues = [{ message: collected.error, room_id: collected.roomId || null }];
      this._render();
      return;
    }
    this._busy = true;
    this._render();
    try {
      const draft = await this._hass.callApi("POST", DRAFT_API, collected.payload);
      const validation = await this._hass.callApi("POST", DRAFT_VALIDATE_API, draft);
      this._firstRun.draft = draft;
      this._firstRun.issues = validation.issues || [];
      this._firstRun.validation = validation;
    } catch (error) {
      this._firstRun.draft = null;
      this._firstRun.issues = [{ message: apiErrorMessage(error) }];
      this._firstRun.validation = { save_allowed: false, status: "blocked" };
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _saveFirstRun() {
    if (this._busy || !this._firstRun.draft || !this._firstRun.validation
        || this._firstRun.validation.status !== "ready"
        || this._firstRun.validation.save_allowed !== true) return;
    this._busy = true;
    this._firstRun.conflict = false;
    this._render();
    let contourSaved = false;
    try {
      await this._hass.callApi("POST", DRAFT_SAVE_API, this._firstRun.draft);
      contourSaved = true;
      this._firstRun.contourSaved = true;
      await this._load();
      const setup = this._settings.setup;
      if (setup && setup.status !== "not_configured") {
        const profiles = await this._hass.callApi("POST", PROFILES_API, {
          contract: PROFILE_CONTRACT,
          rooms: this._firstRunProfiles(),
          setup_revision: setup.setup_revision,
        });
        const scheduleRevision = Number.isSafeInteger(profiles && profiles.setup_revision)
          ? profiles.setup_revision : setup.setup_revision;
        await this._hass.callApi("POST", SCHEDULE_API, {
          confirm_automatic_application: this._firstRun.schedule.enabled === true,
          contract: SCHEDULE_CONTRACT,
          schedule: {
            day_start: this._firstRun.schedule.dayStart,
            enabled: this._firstRun.schedule.enabled === true,
            night_start: this._firstRun.schedule.nightStart,
          },
          setup_revision: scheduleRevision,
        });
        await this._load();
      }
      if (this._firstRunIrDevices().length) {
        this._firstRun.step = "code_source";
        await this._loadFirstRunIrData();
        return;
      }
      this._firstRun.step = "tablet";
    } catch (error) {
      const policy = resolveApiError(error);
      if (contourSaved) {
        this._firstRun.completed = true;
        this._activeSection = "overview";
        this._notice = "Контур сохранён, но профили или расписание не сохранились. Откройте соответствующие вкладки и повторите сохранение.";
      } else if (policy.clientState === "stale") {
        this._firstRun.conflict = true;
      } else {
        this._firstRun.issues = [{ message: policy.safeMessage }];
      }
      this._firstRun.step = "completion";
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _reloadFirstRun() {
    clearFirstRunDraft(this);
    this._firstRunDraftReady = false;
    this._firstRun.completed = false;
    this._firstRun.contourSaved = false;
    this._firstRun.conflict = false;
    this._firstRun.draft = null;
    this._firstRun.issues = [];
    this._firstRun.ir = {
      activeDeviceId: null,
      broadlinkExpanded: false,
      codes: [],
      error: "",
      loading: false,
      manual: { deviceId: null, index: 0, statuses: {} },
      scan: null,
      smartir: { brand: "", deviceCode: "", commandName: "" },
    };
    this._firstRun.options = null;
    this._firstRun.rooms = {};
    this._firstRun.step = "instructions";
    this._firstRun.validRooms.clear();
    await this._load();
  }

  async _openSavedIrCodeSetup() {
    if (!this._firstRunIrDevices().length) return;
    this._firstRun.completed = false;
    this._firstRun.contourSaved = true;
    this._firstRun.deferred = false;
    this._firstRun.step = "code_source";
    await this._loadFirstRunIrData();
  }

  _renderFirstRunProgress(container) {
    const labels = {
      rooms: "Привязка",
      room: "Комната",
      home: "Дом",
      validation: "Проверка",
      save: "Сохранение",
      code_source: "ИК-коды",
      tablet: "Планшет",
      completion: "Завершение",
    };
    const progress = el("nav", "wizard-progress");
    setAttr(progress, "aria-label", "Шаги первичной настройки");
    const current = this._firstRun.step === "success" ? "completion" : this._firstRun.step;
    Object.keys(labels).forEach((step) => {
      const item = el("span", null, labels[step]);
      const index = FIRST_RUN_STEPS.indexOf(step);
      const currentIndex = FIRST_RUN_STEPS.indexOf(current);
      if (step === current) {
        item.className = "is-current";
        setAttr(item, "aria-current", "step");
      }
      if (index < currentIndex) item.className = "is-complete";
      progress.appendChild(item);
    });
    container.appendChild(progress);
  }

  _renderFirstRun(container, setup) {
    container.innerHTML = "";
    this._firstRunFields = null;
    if (this._firstRun.step !== "instructions") this._renderFirstRunProgress(container);
    const card = el("article", "card");
    container.appendChild(card);
    if (this._firstRun.step === "instructions") {
      card.appendChild(el("h2", null, "Климатический контур ещё не настроен"));
      card.appendChild(el("p", "section-intro", "Мастер соберёт климатические устройства по комнатам и поможет быстро разобрать устройства без комнаты."));
      const savedRoomCount = Number(setup && setup.summary && setup.summary.room_count) || 0;
      const savedDeviceCount = Number(setup && setup.summary && setup.summary.device_count) || 0;
      const storageState = el("div", "first-run-storage-state");
      const storageStateHead = el("div", "first-run-storage-state-head");
      storageStateHead.appendChild(el("span", "eyebrow", "Состояние конфигурации"));
      storageStateHead.appendChild(el(
        "strong",
        null,
        `${savedRoomCount} ${this._roomCountWord(savedRoomCount)} · ${savedDeviceCount} ${savedDeviceCount === 1 ? "устройство" : "устройств"}`
      ));
      storageState.appendChild(storageStateHead);
      storageState.appendChild(el(
        "p",
        null,
        "В текущем хранилище Home Assistant не найден сохранённый климатический контур. Поэтому открыт мастер, а не рабочая панель."
      ));
      storageState.appendChild(el(
        "p",
        "muted",
        "Если контур уже настраивали, сначала проверьте резервную копию Home Assistant. Новый контур не создаётся автоматически и не заменяет прежние настройки."
      ));
      card.appendChild(storageState);
      const list = el("ol", "reasons");
      [
        "Сначала проверьте найденные комнаты и физические устройства.",
        "Устройства без области сначала сохраните в комнате Home Assistant — эта привязка станет общей для HA и Hausman Hub.",
        "Затем откройте каждую используемую комнату и проверьте функции устройства.",
      ].forEach((text) => list.appendChild(el("li", null, text)));
      card.appendChild(list);
      card.appendChild(el("div", "candidate-room-warning", "Комната устройства хранится в Home Assistant. Изменения применятся только после нажатия «Сохранить привязки в Home Assistant»."));
      const actions = el("div", "actions");
      const start = el("button", null, "Начать настройку");
      start.disabled = this._busy;
      start.addEventListener("click", () => this._startFirstRun());
      const later = el("button", "secondary", "Настроить позже");
      later.disabled = this._busy;
      later.addEventListener("click", () => this._deferFirstRun());
      actions.appendChild(start);
      actions.appendChild(later);
      card.appendChild(actions);
      return;
    }
    if (!this._firstRun.options && !["code_source", "tablet", "completion"].includes(this._firstRun.step)) {
      card.appendChild(el("h2", null, "Подготовка мастера"));
      if (this._firstRun.optionsError) {
        card.appendChild(el("div", "field-error", "Не удалось загрузить комнаты и устройства."));
        const retry = el("button", "secondary", "Повторить загрузку");
        retry.addEventListener("click", () => this._loadFirstRunOptions(true));
        card.appendChild(retry);
      } else {
        card.appendChild(el("div", "muted", "Загрузка областей, устройств и датчиков…"));
      }
      return;
    }
    if (this._firstRun.step === "rooms") this._renderFirstRunRooms(card);
    if (this._firstRun.step === "room") this._renderFirstRunRoom(card);
    if (this._firstRun.step === "home") this._renderFirstRunHome(card);
    if (this._firstRun.step === "validation") this._renderFirstRunValidation(card);
    if (this._firstRun.step === "save") this._renderFirstRunCompletion(card);
    if (this._firstRun.step === "code_source") this._renderFirstRunCodeSource(card);
    if (this._firstRun.step === "tablet") this._renderFirstRunTablet(card);
    if (this._firstRun.step === "completion") this._renderFirstRunCompletion(card);
    if (this._firstRun.step === "success") this._renderFirstRunSuccess(card);
  }

  _renderFirstRunRooms(card) {
    renderFirstRunAreaBinding.call(this, card, {
      el, normalizedText, selectField, svgIcon, ZIGBEE2MQTT_IMAGE_PATTERN,
    });
  }

  _renderFirstRunRoom(card) {
    renderFirstRunRoom.call(this, card, {
      el, setAttr, numberField, selectField, normalizedText,
      ACTIVE_DEVICE_TYPES, STRATEGY_ORDER, ROOM_SETUP_PANES,
    });
  }
  _renderFirstRunHome(card) {
    card.appendChild(el("h2", null, "Параметры дома"));
    card.appendChild(el("div", "section-intro", "Выберите общие сигналы и пороги отопления. Они сохраняются отдельным безопасным запросом без команд устройствам."));
    if (!this._firstRun.home) {
      const saved = (this._settings.home && this._settings.home.home) || {};
      this._firstRun.home = {
        central_heating_entity_id: saved.central_heating_entity_id || null,
        heating_lockout_high: saved.heating_lockout_high === undefined ? 18 : saved.heating_lockout_high,
        heating_lockout_low: saved.heating_lockout_low === undefined ? 16 : saved.heating_lockout_low,
        central_heating_temperature_on: saved.central_heating_temperature_on === undefined ? 35 : saved.central_heating_temperature_on,
        central_heating_temperature_off: saved.central_heating_temperature_off === undefined ? 30 : saved.central_heating_temperature_off,
        air_conditioner_minimum_outdoor_temperature:
          saved.air_conditioner_minimum_outdoor_temperature === undefined
            ? -5 : saved.air_conditioner_minimum_outdoor_temperature,
        outdoor_temperature_entity_id: saved.outdoor_temperature_entity_id || null,
        outdoor_temperature_entity_ids: Array.isArray(saved.outdoor_temperature_entity_ids)
          && saved.outdoor_temperature_entity_ids.length
          ? saved.outdoor_temperature_entity_ids
          : (saved.outdoor_temperature_entity_id ? [saved.outdoor_temperature_entity_id] : []),
        presence_entity_id: saved.presence_entity_id || null,
      };
    }
    const home = this._firstRun.home;
    const candidates = (this._settings.home && this._settings.home.candidates) || {};
    const pickers = {};
    HOME_SIGNAL_BINDINGS.forEach(({ key, title, helper, purpose, recommendation, kind }) => {
      const picker = kind === "outdoor_temperature" ? this._priorityChoicePicker({
        candidates: candidates[kind] || [],
        current: home.outdoor_temperature_entity_ids,
        helper,
        purpose,
        recommendation: "Источники проверяются сверху вниз. Первый доступный становится активным, остальные остаются резервными.",
        pickerId: `first-run-home-${key}`,
        onChange: (values) => {
          home.outdoor_temperature_entity_ids = values;
          home.outdoor_temperature_entity_id = values[0] || null;
          this._firstRunInvalidate();
        },
        signalKind: kind,
        title,
      }) : this._singleChoicePicker({
        candidates: candidates[kind] || [],
        current: home[key],
        helper,
        purpose,
        recommendation,
        pickerId: `first-run-home-${key}`,
        onChange: (value) => {
          home[key] = value || null;
          this._firstRunInvalidate();
        },
        signalKind: kind,
        title,
        });
      card.appendChild(picker.root);
      pickers[key] = picker;
    });
    const heatingThresholds = createHeatingTemperatureFields({
      onValue: home.central_heating_temperature_on,
      offValue: home.central_heating_temperature_off,
      onChange: () => {
        const values = heatingThresholds.values();
        home.central_heating_temperature_on = values.on;
        home.central_heating_temperature_off = values.off;
        clearError();
        this._firstRunInvalidate();
      },
    }, { el, numberField });
    card.appendChild(heatingThresholds.root);
    card.appendChild(el("h3", "threshold-heading", "Погодная блокировка отопления"));
    card.appendChild(el(
      "div",
      "muted threshold-intro",
      "Необязательная защита: Hausman Hub меняет разрешение на нагрев только после пересечения указанных порогов."
    ));
    const clearError = () => { this._firstRun.homeError = ""; card.querySelector(".field-error")?.remove(); };
    const high = numberField(home.heating_lockout_high, -40, 60, 0.5, () => {
      home.heating_lockout_high = high.value;
      clearError();
      this._firstRunInvalidate();
    });
    const highRow = el("label", "form-field", "Блокировать нагрев теплее, °C");
    highRow.appendChild(high);
    card.appendChild(highRow);
    const low = numberField(home.heating_lockout_low, -40, 60, 0.5, () => {
      home.heating_lockout_low = low.value;
      clearError();
      this._firstRunInvalidate();
    });
    const lowRow = el("label", "form-field", "Разрешать нагрев холоднее, °C");
    lowRow.appendChild(low);
    card.appendChild(lowRow);
    card.appendChild(el("div", "muted field-help", LOCKOUT_HELP));
    card.appendChild(el("h3", "threshold-heading", "Защита кондиционеров от холода"));
    card.appendChild(el(
      "div",
      "muted threshold-intro",
      "При достижении критической наружной температуры Hausman Hub не запускает кондиционеры автоматически и выключает уже работающие — даже включённые вручную."
    ));
    const airConditionerMinimum = numberField(
      home.air_conditioner_minimum_outdoor_temperature, -40, 60, 0.5,
      () => {
        home.air_conditioner_minimum_outdoor_temperature = airConditionerMinimum.value;
        clearError();
        this._firstRunInvalidate();
      }
    );
    const airConditionerMinimumRow = el(
      "label", "form-field", "Критический минимум на улице, °C"
    );
    airConditionerMinimumRow.appendChild(airConditionerMinimum);
    card.appendChild(airConditionerMinimumRow);
    card.appendChild(el(
      "div", "muted field-help",
      "Защита срабатывает при указанной температуре и ниже. Рекомендуемое значение для большинства бытовых кондиционеров — −5 °C; точный предел сверяйте с паспортом модели."
    ));
    if (this._firstRun.homeError) card.appendChild(el("div", "field-error", this._firstRun.homeError));
    const actions = el("div", "actions");
    const back = el("button", "secondary", "Назад к комнатам");
    back.disabled = this._busy;
    back.addEventListener("click", () => {
      this._firstRun.step = "rooms";
      this._render();
    });
    const next = el("button", null, "Продолжить к проверке");
    next.disabled = this._busy;
    next.addEventListener("click", () => {
      Object.keys(pickers).forEach((key) => (
        applyHomeSignalSelection(home, key, pickers[key].value())
      ));
      this._saveFirstRunHome();
    });
    actions.appendChild(back);
    actions.appendChild(next);
    card.appendChild(actions);
  }

  _renderFirstRunValidation(card) {
    card.appendChild(el("h2", null, "Проверка настройки"));
    card.appendChild(el("div", "section-intro", "Перед сохранением Hausman Hub проверит все выбранные комнаты, устройства и границы одним черновиком."));
    const validation = this._firstRun.validation;
    if (validation) {
      const ready = validation.status === "ready" && validation.save_allowed === true;
      const issues = this._firstRun.issues || [];
      const report = el("section", `wizard-report validation-report ${ready ? "is-ready" : "is-blocked"}`);
      const reportHead = el("div", "validation-report-head");
      reportHead.appendChild(el("span", "validation-report-icon", ready ? "✓" : "!"));
      const reportCopy = el("div", "validation-report-copy");
      reportCopy.appendChild(el(
        "strong",
        null,
        ready
          ? (issues.length ? "Готово с замечаниями" : "Настройка готова")
          : "Нужно исправить настройки"
      ));
      reportCopy.appendChild(el(
        "span",
        null,
        ready
          ? "Блокирующих ошибок нет — можно перейти к сохранению."
          : "Исправьте отмеченные комнаты и повторите проверку."
      ));
      reportHead.appendChild(reportCopy);
      report.appendChild(reportHead);
      if (issues.length) {
        const list = el("div", "validation-issue-list");
        issues.forEach((issue) => {
          const line = el("div", `validation-issue-row ${issue.level === "warning" ? "is-warning" : "is-error"}`);
          line.appendChild(el("span", "validation-issue-marker", issue.level === "warning" ? "!" : "×"));
          const issueCopy = el("div", "validation-issue-copy");
          issueCopy.appendChild(el(
            "strong",
            null,
            issue.level === "warning" ? "Требует внимания" : "Нужно исправить"
          ));
          issueCopy.appendChild(el(
            "span",
            issue.level === "warning" ? "issue-warning" : null,
            issue.message || "Проверьте настройку."
          ));
          line.appendChild(issueCopy);
          if (issue.room_id) {
            const fix = el("button", "secondary validation-issue-action", "Открыть комнату");
            fix.addEventListener("click", () => this._openFirstRunRoom(issue.room_id));
            line.appendChild(fix);
          }
          list.appendChild(line);
        });
        report.appendChild(list);
      } else if (ready) {
        report.appendChild(el("div", "validation-ready-note", "Все выбранные комнаты и устройства проверены."));
      }
      card.appendChild(report);
    }
    const actions = el("div", "actions validation-actions");
    const back = el("button", "secondary", "Назад к параметрам дома");
    back.disabled = this._busy;
    back.addEventListener("click", () => {
      this._firstRun.step = "home";
      this._render();
    });
    const mainActions = el("div", "validation-actions-main");
    const check = el("button", validation ? "secondary" : null, validation ? "Проверить повторно" : "Проверить настройку");
    check.disabled = this._busy;
    check.addEventListener("click", () => this._validateFirstRun());
    actions.appendChild(back);
    mainActions.appendChild(check);
    if (validation && validation.status === "ready" && validation.save_allowed === true) {
      const next = el("button", null, "Перейти к сохранению");
      next.disabled = this._busy;
      next.addEventListener("click", () => {
        this._firstRun.step = "save";
        this._render();
      });
      mainActions.appendChild(next);
    }
    actions.appendChild(mainActions);
    card.appendChild(actions);
  }

  _renderFirstRunCodeSource(card) {
    const ir = this._firstRun.ir;
    const devices = this._firstRunIrDevices();
    const device = this._firstRunActiveIrDevice();
    card.appendChild(el("h2", null, "Источник IR-кодов"));
    card.appendChild(el("div", "section-intro", "Добавьте ИК-коды из базы, Broadlink или через обучение."));
    if (!device) {
      card.appendChild(el("div", "wizard-hint", "Для устройств с каналом universal_ir не найден безопасный remote entity id. Источники ИК-кодов пропущены."));
      const next = el("button", null, "Продолжить к подключению планшета");
      next.addEventListener("click", () => {
        this._firstRun.step = "tablet";
        this._render();
      });
      card.appendChild(next);
      return;
    }
    if (devices.length > 1) {
      const devicePicker = selectField(
        devices.map((item) => ({ label: item.name, value: item.deviceId })),
        device.deviceId,
        () => {
          ir.activeDeviceId = devicePicker.value;
          ir.manual = { deviceId: null, index: 0, statuses: {} };
          this._render();
        }
      );
      const deviceRow = el("label", "form-field", "Устройство");
      deviceRow.appendChild(devicePicker);
      card.appendChild(deviceRow);
    }
    const summary = el("div", "ir-device-summary");
    summary.appendChild(el("strong", null, device.name));
    summary.appendChild(el("span", "chip", "Канал: universal_ir"));
    summary.appendChild(el("small", "muted", device.remoteEntityId));
    card.appendChild(summary);
    if (ir.loading && !ir.scan) {
      card.appendChild(el("div", "muted", "Загрузка источников ИК-кодов…"));
      return;
    }
    if (ir.error) card.appendChild(el("div", "field-error", ir.error));
    const scan = ir.scan || {};
    const smartirBrands = Array.isArray(scan.smartir_catalog) ? scan.smartir_catalog : [];
    const smartir = el("section", "ir-source-card is-recommended");
    smartir.appendChild(el("h3", null, "База кодов SmartIR"));
    if (!smartirBrands.length) {
      smartir.appendChild(el("div", "muted", "Источник SmartIR недоступен или не содержит кодов, он пропущен."));
    } else {
      const selectedBrand = smartirBrands.find((brand) => brand.brand === ir.smartir.brand) || smartirBrands[0];
      ir.smartir.brand = selectedBrand.brand;
      const selectedModel = (selectedBrand.models || []).find((model) => (
        model.device_code === ir.smartir.deviceCode
      )) || selectedBrand.models[0];
      ir.smartir.deviceCode = selectedModel.device_code;
      const selectedCommand = (selectedModel.commands || []).find((command) => (
        command.command_name === ir.smartir.commandName
      )) || selectedModel.commands[0];
      ir.smartir.commandName = selectedCommand.command_name;
      const brandPicker = selectField(
        smartirBrands.map((brand) => ({ label: brand.brand, value: brand.brand })),
        selectedBrand.brand,
        () => {
          ir.smartir.brand = brandPicker.value;
          ir.smartir.deviceCode = "";
          ir.smartir.commandName = "";
          this._render();
        }
      );
      const brandRow = el("label", "form-field", "Бренд");
      brandRow.appendChild(brandPicker);
      smartir.appendChild(brandRow);
      const modelPicker = selectField(
        selectedBrand.models.map((model) => ({ label: model.model || model.name, value: model.device_code })),
        selectedModel.device_code,
        () => {
          ir.smartir.deviceCode = modelPicker.value;
          ir.smartir.commandName = "";
          this._render();
        }
      );
      const modelRow = el("label", "form-field", "Модель");
      modelRow.appendChild(modelPicker);
      smartir.appendChild(modelRow);
      const commandPicker = selectField(
        selectedModel.commands.map((command) => ({ label: command.command_name, value: command.command_name })),
        selectedCommand.command_name,
        () => { ir.smartir.commandName = commandPicker.value; }
      );
      const commandRow = el("label", "form-field", "Команда");
      commandRow.appendChild(commandPicker);
      smartir.appendChild(commandRow);
      const actions = el("div", "actions");
      const test = el("button", "secondary", "Тест-отправка");
      test.disabled = ir.loading;
      const currentCommand = () => (selectedModel.commands || []).find((command) => (
        command.command_name === ir.smartir.commandName
      )) || selectedModel.commands[0];
      test.addEventListener("click", () => this._testFirstRunIrCode(device, currentCommand()));
      const importCode = el("button", null, "Импортировать");
      importCode.disabled = ir.loading;
      importCode.addEventListener("click", () => this._importFirstRunIrCode(device, currentCommand(), "smartir"));
      actions.appendChild(test);
      actions.appendChild(importCode);
      smartir.appendChild(actions);
    }
    smartir.appendChild(el("div", "muted", "Данные SmartIR только читаются, записи в SmartIR не выполняются."));
    card.appendChild(smartir);

    const broadlink = el("section", "ir-source-card");
    broadlink.appendChild(el("h3", null, "Выученные коды Broadlink"));
    const broadlinkToggle = el(
      "button",
      "secondary",
      ir.broadlinkExpanded ? "Скрыть команды" : "Показать команды"
    );
    broadlinkToggle.disabled = ir.loading;
    broadlinkToggle.addEventListener("click", () => {
      ir.broadlinkExpanded = !ir.broadlinkExpanded;
      this._render();
    });
    broadlink.appendChild(broadlinkToggle);
    if (ir.broadlinkExpanded) {
      const remotes = Array.isArray(scan.broadlink_catalog) ? scan.broadlink_catalog : [];
      if (!remotes.length) {
        broadlink.appendChild(el("div", "muted", "Сохранённых команд Broadlink не найдено."));
      }
      remotes.forEach((remote) => {
        const block = el("div", "ir-code-list");
        block.appendChild(el("strong", null, remote.remote_entity_id));
        (remote.commands || []).forEach((command) => {
          const row = el("div", "ir-code-row");
          row.appendChild(el("span", null, command.command_name));
          const importCode = el("button", "secondary", "Импортировать");
          importCode.disabled = ir.loading;
          importCode.addEventListener("click", () => this._importFirstRunIrCode(device, command, "broadlink"));
          row.appendChild(importCode);
          block.appendChild(row);
        });
        broadlink.appendChild(block);
      });
    }
    broadlink.appendChild(el("div", "muted", "Чтение только из хранилища пульта; при ошибке чтения источник пропускается."));
    card.appendChild(broadlink);

    const manual = el("section", "ir-source-card is-muted");
    manual.appendChild(el("h3", null, "Ручное обучение"));
    manual.appendChild(el("div", "muted", "Нажмите кнопку, затем отправьте указанную команду оригинальным пультом."));
    const commands = this._firstRunIrManualCommands(device);
    if (ir.manual.deviceId !== device.deviceId) {
      ir.manual = { deviceId: device.deviceId, index: 0, statuses: {} };
    }
    const progress = el("div", "ir-manual-progress");
    commands.forEach((command, index) => {
      const row = el("div");
      row.appendChild(el("span", null, command.label));
      const status = ir.manual.statuses[command.commandName];
      if (status === "ready") row.appendChild(el("span", "status-badge is-ready", "Готово"));
      if (status === "learning") row.appendChild(el("span", "status-badge", "Ожидание сигнала"));
      if (status === "timeout") row.appendChild(el("span", "status-badge is-attention", "Время ожидания истекло"));
      if (status === "error") row.appendChild(el("span", "status-badge is-attention", "Ошибка обучения"));
      if (!status && index > ir.manual.index) row.appendChild(el("span", "muted", "Далее"));
      progress.appendChild(row);
    });
    manual.appendChild(progress);
    const currentCommand = commands[ir.manual.index];
    if (currentCommand) {
      const status = ir.manual.statuses[currentCommand.commandName];
      const learn = el(
        "button",
        "secondary",
        status === "timeout" ? "Повторить обучение" : ir.manual.index === 0 ? "Начать обучение" : "Продолжить обучение"
      );
      learn.disabled = ir.loading;
      learn.addEventListener("click", () => this._learnFirstRunIrCode(device));
      manual.appendChild(learn);
      if (status === "timeout") {
        manual.appendChild(el("div", "muted", "Время ожидания истекло. Убедитесь, что пульт готов к обучению, и повторите команду."));
      }
    }
    card.appendChild(manual);

    const imported = el("section", "wizard-section");
    imported.appendChild(el("h3", null, "Импортированные коды"));
    const codes = (ir.codes || []).filter((code) => code.device_id === device.deviceId);
    if (!codes.length) imported.appendChild(el("div", "muted", "Для этого устройства пока нет импортированных кодов."));
    const list = el("div", "ir-code-list");
    codes.forEach((code) => {
      const row = el("div", "ir-code-row");
      const identity = el("span");
      identity.appendChild(el("strong", null, code.command_name));
      identity.appendChild(el("span", "chip", code.source));
      row.appendChild(identity);
      const test = el("button", "secondary", "Тест-отправка");
      test.disabled = ir.loading;
      test.addEventListener("click", () => this._testFirstRunImportedIrCode(code));
      row.appendChild(test);
      const remove = el("button", "secondary ir-icon-action");
      setAttr(remove, "aria-label", `Удалить код ${code.command_name}`);
      setAttr(remove, "title", "Удалить код");
      remove.disabled = ir.loading;
      remove.appendChild(svgIcon("trash"));
      remove.addEventListener("click", () => this._deleteFirstRunIrCode(code));
      row.appendChild(remove);
      list.appendChild(row);
    });
    imported.appendChild(list);
    card.appendChild(imported);
    card.appendChild(el("div", "wizard-warning", "Если код команды не найден, контур покажет ограничение «ir_command_not_learned»: температура не подставляется автоматически."));
    const actions = el("div", "actions");
    const next = el("button", null, "Продолжить к подключению планшета");
    next.disabled = ir.loading;
    next.addEventListener("click", () => {
      this._firstRun.step = "tablet";
      this._render();
    });
    actions.appendChild(next);
    card.appendChild(actions);
  }

  _renderFirstRunTablet(card) {
    card.appendChild(el("h2", null, "Подключение планшета"));
    card.appendChild(el("div", "section-intro", "Укажите адрес Home Assistant и личный токен отдельного пользователя планшета. Подключение идёт напрямую к Hausman Hub."));
    const url = window.location && window.location.origin ? window.location.origin : "Адрес этого Home Assistant";
    const address = el("input", "wizard-tablet-url");
    address.type = "text";
    address.readOnly = true;
    address.value = url;
    setAttr(address, "aria-label", "Базовый адрес Home Assistant для планшета");
    card.appendChild(address);
    const copy = el("button", "secondary", "Скопировать адрес");
    copy.addEventListener("click", async () => {
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        await navigator.clipboard.writeText(address.value);
        this._notice = "Адрес скопирован.";
        this._render();
      }
    });
    card.appendChild(copy);
    const endpoints = el("ul", "reasons");
    [
      "GET /api/hausman_hub/v1/capabilities — возможности API.",
      "GET /api/hausman_hub/v1/dashboard — сводка дома.",
      "GET /api/hausman_hub/v1/events — живые события.",
      "POST /api/hausman_hub/v1/device-actions — команды с подтверждением.",
    ].forEach((text) => endpoints.appendChild(el("li", null, text)));
    card.appendChild(endpoints);
    const actions = el("div", "actions");
    const back = el("button", "secondary", "Назад к проверке");
    back.disabled = this._busy;
    back.addEventListener("click", () => {
      this._firstRun.step = this._firstRunIrDevices().length ? "code_source" : "completion";
      this._render();
    });
    const next = el("button", null, "Перейти к завершению");
    next.disabled = this._busy;
    next.addEventListener("click", () => {
      this._firstRun.step = "completion";
      this._render();
    });
    actions.appendChild(back);
    actions.appendChild(next);
    card.appendChild(actions);
  }

  _renderFirstRunCompletion(card) {
    card.appendChild(el("h2", null, "Завершение настройки"));
    if (this._firstRun.contourSaved) {
      card.appendChild(el("div", "section-intro", "Климатический контур сохранён. Источники ИК-кодов настроены на сохранённом устройстве."));
      const finish = el("button", null, "Открыть панель");
      finish.disabled = this._busy;
      finish.addEventListener("click", () => {
        clearFirstRunDraft(this);
        this._firstRun.completed = true;
        this._activeSection = "overview";
        this._notice = "Настройка сохранена. Команды устройствам не отправлялись.";
        this._render();
      });
      card.appendChild(finish);
      return;
    }
    if (this._firstRun.conflict) {
      card.appendChild(el("div", "field-error", "Настройки изменились в другом окне. Обновите мастер, чтобы получить актуальные области и ревизию."));
      const reload = el("button", "secondary", "Обновить мастер");
      reload.disabled = this._busy;
      reload.addEventListener("click", () => this._reloadFirstRun());
      card.appendChild(reload);
      return;
    }
    const validation = this._firstRun.validation;
    const ready = validation && validation.status === "ready" && validation.save_allowed === true;
    card.appendChild(el("div", "section-intro", ready
      ? `Будет сохранено комнат: ${this._firstRun.validRooms.size}. Команды устройствам не отправляются.`
      : "Сначала вернитесь к проверке и получите успешный результат."));
    (this._firstRun.issues || []).forEach((issue) => {
      card.appendChild(el(
        "div",
        issue.level === "warning" ? "field-error issue-warning" : "field-error",
        issue.message || "Проверьте настройку."
      ));
    });
    const actions = el("div", "actions");
    const back = el("button", "secondary", "Назад к проверке");
    back.disabled = this._busy;
    back.addEventListener("click", () => {
      this._firstRun.step = "validation";
      this._render();
    });
    const save = el("button", null, "Сохранить настройку");
    save.disabled = this._busy || !ready;
    save.title = save.disabled ? "Сохранение доступно после успешной полной проверки." : "Сохранить проверенный климатический контур.";
    save.addEventListener("click", () => this._saveFirstRun());
    actions.appendChild(back);
    actions.appendChild(save);
    card.appendChild(actions);
  }

  _renderFirstRunSuccess(card) {
    card.appendChild(el("h2", null, "Настройка сохранена"));
    card.appendChild(el("div", "wizard-success", "Контур создан. Сейчас откроется обычная панель Hausman Hub."));
  }

  _renderContourWizard(container, setup) {
    if (!setup) {
      container.appendChild(el("div", "card muted", "Настройка контура временно недоступна."));
      return;
    }
    const configured = setup.status !== "not_configured";
    if (configured && !this._wizard.open) {
      const card = el("div", "card contour-config-card");
      const head = el("div", "contour-card-head");
      const icon = el("span", "contour-card-icon");
      icon.appendChild(svgIcon("thermometer"));
      head.appendChild(icon);
      const copy = el("div", "contour-card-copy");
      copy.appendChild(el("h3", null, setup.name || "Климатический контур"));
      copy.appendChild(el("p", "muted", "Текущая конфигурация сохранена и доступна для редактирования"));
      head.appendChild(copy);
      head.appendChild(el("span", "status-badge is-ready", "Настроен"));
      card.appendChild(head);
      const modes = (setup.display_names && setup.display_names.modes) || {};
      const facts = el("div", "contour-facts");
      this._row(facts, "Режим", modes[setup.mode] || this._roomModeName(setup.mode));
      const summary = setup.summary || {};
      this._row(facts, "Комнат", summary.room_count || 0);
      this._row(facts, "Устройств", summary.device_count || 0);
      card.appendChild(facts);
      (setup.issues || []).forEach((issue) => {
        if (issue && issue.message) card.appendChild(el("div", "wizard-issues", issue.message));
      });
      const edit = el("button", null, "Изменить контур");
      edit.disabled = this._busy || setup.editing_allowed !== true;
      edit.addEventListener("click", () => this._openWizard(setup));
      const actions = el("div", "actions contour-card-actions");
      actions.appendChild(edit);
      if (this._firstRunIrDevices().length) {
        const irCodes = el("button", "secondary", "Настроить IR-коды");
        irCodes.disabled = this._busy;
        irCodes.addEventListener("click", () => this._openSavedIrCodeSetup());
        actions.appendChild(irCodes);
      }
      card.appendChild(actions);
      if (setup.editing_allowed !== true) {
        card.appendChild(
          el("div", "muted", "Редактирование недоступно: данные устройств устарели или изменились.")
        );
      }
      container.appendChild(card);
      return;
    }

    if (!this._wizard.options) {
      const card = el("div", "card");
      card.appendChild(el(
        "h3", null,
        configured ? "Изменение климатического контура" : "Создание климатического контура"
      ));
      if (this._wizard.optionsError) {
        card.appendChild(el("div", "muted", "Не удалось загрузить комнаты и устройства."));
        const retry = el("button", "secondary", "Повторить загрузку");
        retry.disabled = this._wizard.loading;
        retry.addEventListener("click", () => this._loadWizardOptions(true));
        card.appendChild(retry);
      } else {
        card.appendChild(el("div", "muted", "Загрузка комнат и устройств..."));
        if (!this._wizard.loading) this._loadWizardOptions();
      }
      container.appendChild(card);
      return;
    }
    this._renderWizardForm(container, setup, this._wizard.options);
  }

  async _loadWizardOptions(force = false) {
    if (!this._hass || this._wizard.loading) return;
    if (this._wizard.options && !force) return;
    this._wizard.loading = true;
    this._wizard.optionsError = false;
    if (force) this._wizard.options = null;
    try {
      this._wizard.options = await this._hass.callApi("GET", DRAFT_API);
      this._wizard.optionsError = false;
    } catch (error) {
      this._wizard.options = null;
      this._wizard.optionsError = true;
    } finally {
      this._wizard.loading = false;
    }
    if (!this._dirty.wizard) this._render();
  }

  _openWizard(setup) {
    if (setup.status !== "not_configured" && setup.editing_allowed !== true) return;
    this._activateSection("climate");
    this._activateClimateView("contour");
    this._wizard.open = true;
    this._expandedWizardRooms.clear();
    this._wizard.setupRevision = setup.setup_revision;
    this._wizard.optionsError = false;
    this._wizard.draft = null;
    this._wizard.validation = null;
    this._wizard.fingerprint = null;
    this._dirty.wizard = false;
    this._render();
  }

  _cancelWizard() {
    this._wizard.open = false;
    this._expandedWizardRooms.clear();
    this._wizard.setupRevision = null;
    this._wizard.draft = null;
    this._wizard.validation = null;
    this._wizard.fingerprint = null;
    this._wizardFields = null;
    this._wizardIssues = null;
    this._wizardButtons = null;
    this._dirty.wizard = false;
    this._render();
  }

  _renderWizardForm(container, setup, options) {
    if (this._wizard.setupRevision === null) {
      this._wizard.setupRevision = setup.setup_revision;
    }
    const editing = setup.status !== "not_configured";
    const currentRooms = new Map((setup.rooms || []).map((room) => [room.id, room]));
    const deviceTypes = (options.display_names && options.display_names.device_types) || {};
    const strategies = (options.display_names && options.display_names.strategies) || {};
    const modes = (options.display_names && options.display_names.modes) || {};
    const fields = { rooms: {}, candidateBoxes: {}, controls: [], name: null, mode: null };
    const issues = { rooms: {}, global: null, success: null };
    const card = el("div", "card");
    card.appendChild(el(
      "h3", null,
      editing ? "Изменение климатического контура" : "Создание климатического контура"
    ));
    card.appendChild(el(
      "div",
      "section-intro",
      "Выберите комнаты, затем раскройте только те карточки, в которых нужно проверить цели и устройства."
    ));

    const name = el("input");
    name.type = "text";
    name.value = setup.name || "Климат";
    name.addEventListener("input", () => this._wizardChanged());
    const nameRow = el("label", "form-field", "Название контура");
    nameRow.appendChild(name);
    card.appendChild(nameRow);

    const mode = selectField(
      CONTOUR_MODE_ORDER.map((code) => ({ value: code, label: modes[code] || code })),
      setup.mode || "observe",
      () => this._wizardChanged()
    );
    const modeRow = el("label", "form-field", "Режим");
    modeRow.appendChild(mode);
    card.appendChild(modeRow);
    fields.name = name;
    fields.mode = mode;
    fields.controls.push(name, mode);

    const unassignedCandidates = (options.devices || []).filter((candidate) => (
      candidate.room_id === ""
      && !candidate.suggested_room_id
      && candidate.can_add === true
    ));
    if (unassignedCandidates.length) {
      card.appendChild(el(
        "div",
        "candidate-room-warning",
        `Не показано устройств без комнаты: ${unassignedCandidates.length}. Назначьте им зону в Home Assistant, чтобы они появились только в нужной комнате.`
      ));
    }

    (options.rooms || []).forEach((room) => {
      const currentRoom = currentRooms.get(room.id);
      const currentDevices = new Map(
        (((currentRoom && currentRoom.devices) || [])).map((device) => [device.candidate_id, device])
      );
      const candidates = (options.devices || []).filter((candidate) => (
        currentDevices.has(candidate.candidate_id)
        || (
          candidate.can_add === true
          && (
            candidate.room_id === room.id
            || (
              candidate.room_id === ""
              && candidate.suggested_room_id === room.id
            )
          )
        )
      ));
      const suggested = !editing && candidates.some(
        (candidate) => candidate.can_add === true && candidate.suggested_room_id === room.id
      );
      const canUseRoom = room.selectable === true || Boolean(currentRoom);
      const block = el("div", "room-block");
      setAttr(block, "data-room-id", room.id);
      const summary = el("div", "room-summary");
      const include = el("input");
      include.type = "checkbox";
      include.value = room.id;
      include.checked = Boolean(currentRoom) || suggested;
      include.disabled = !canUseRoom;
      const includeRow = el("label", "checkbox-field");
      includeRow.appendChild(include);
      includeRow.appendChild(el("span", null, room.name || room.id));
      summary.appendChild(includeRow);
      const summaryMeta = el("div", "room-summary-meta");
      summary.appendChild(summaryMeta);
      const summaryText = el("span");
      summaryMeta.appendChild(summaryText);
      const errorBadge = el("span", "room-error-badge", "Требует внимания");
      errorBadge.hidden = true;
      summaryMeta.appendChild(errorBadge);
      const expander = el("button", "secondary room-expander", "Настроить");
      expander.type = "button";
      setAttr(expander, "aria-expanded", "false");
      summary.appendChild(expander);
      block.appendChild(summary);
      const editor = el("div", "room-editor");
      editor.id = `hausman-wizard-room-${room.id}`;
      editor.hidden = true;
      setAttr(expander, "aria-controls", editor.id);
      block.appendChild(editor);

      const profiles = (currentRoom && currentRoom.profiles) || {};
      const activeProfile = ["day", "night"].includes(profiles.active_profile)
        ? profiles.active_profile : "day";
      const activeSettings = profiles[activeProfile] || {};
      const temperature = numberField(
        activeSettings.target_temperature === undefined ? 22 : activeSettings.target_temperature,
        18, 28, 0.5, () => this._wizardChanged()
      );
      const humidity = numberField(
        activeSettings.target_humidity === undefined ? 45 : activeSettings.target_humidity,
        30, 70, 1, () => this._wizardChanged()
      );
      const strategy = selectField(
        STRATEGY_ORDER.map((code) => ({ value: code, label: strategies[code] || code })),
        activeSettings.strategy || "normal",
        () => this._wizardChanged()
      );
      const temperatureLabel = editing
        ? "Активный профиль: целевая температура, °C" : "Целевая температура, °C";
      const temperatureRow = el("label", "form-field", temperatureLabel);
      temperatureRow.appendChild(temperature);
      editor.appendChild(temperatureRow);
      editor.appendChild(el("div", "muted field-help", "Допустимо 18–28 °C, шаг 0,5 °C."));
      const humidityLabel = editing
        ? "Активный профиль: целевая влажность, %" : "Целевая влажность, %";
      const humidityRow = el("label", "form-field", humidityLabel);
      humidityRow.appendChild(humidity);
      editor.appendChild(humidityRow);
      editor.appendChild(el("div", "muted field-help", "Допустимо 30–70 %, шаг 1 %."));
      const strategyRow = el("label", "form-field", "Стратегия");
      strategyRow.appendChild(strategy);
      editor.appendChild(strategyRow);

      const roomFields = {
        include, temperature, humidity, strategy, devices: [], canUseRoom, toggle: null,
        editor, expander, summaryMeta, summaryText, errorBadge, expanded: false, everExpanded: false,
        setExpanded: null, updateSummary: null,
      };
      const appendDevices = (title, allowedTypes) => {
        editor.appendChild(el("h4", null, title));
        const choices = [];
        candidates.forEach((candidate, candidateIndex) => {
          const currentDevice = currentDevices.get(candidate.candidate_id);
          const suggestedTypes = Array.isArray(candidate.suggested_types)
            ? candidate.suggested_types : [];
          const recommended = candidate.recommended_type || suggestedTypes[0];
          suggestedTypes.filter((type) => allowedTypes.has(type)).forEach((type) => {
            const checked = Boolean(
              (currentDevice && currentDevice.type === type)
              || (!editing && candidate.can_add === true
                && candidate.suggested_room_id === room.id && recommended === type)
            );
            choices.push({
              candidate,
              type,
              checked,
              payloadOrder: (
                (ACTIVE_DEVICE_TYPES.has(type) ? 0 : 100000)
                + (candidateIndex * 100)
                + suggestedTypes.indexOf(type)
              ),
            });
          });
        });
        choices.sort((left, right) => (
          Number(right.checked) - Number(left.checked)
          || String(left.candidate.name).localeCompare(String(right.candidate.name), "ru")
        ));
        if (!choices.length) {
          editor.appendChild(el("div", "muted", "Подходящих устройств нет."));
          return;
        }
        const search = el("input", "entity-search");
        search.type = "search";
        search.placeholder = "Найти устройство";
        setAttr(search, "aria-label", `Поиск: ${title.toLocaleLowerCase("ru")}`);
        editor.appendChild(search);
        const groups = el("div", "entity-groups");
        const groupedChoices = new Map();
        choices.forEach((choice) => {
          const candidate = choice.candidate;
          const groupId = candidate.device_group_id || `candidate:${candidate.candidate_id}`;
          if (!groupedChoices.has(groupId)) {
            groupedChoices.set(groupId, {
              groupId,
              choices: [],
              deviceName: candidate.device_name || candidate.name,
              manufacturer: candidate.manufacturer || "",
              model: candidate.model || "",
              imageUrl: (
                typeof candidate.image_url === "string"
                && ZIGBEE2MQTT_IMAGE_PATTERN.test(candidate.image_url)
              ) ? candidate.image_url : "",
            });
          }
          groupedChoices.get(groupId).choices.push(choice);
        });
        const deviceGroups = Array.from(groupedChoices.values()).sort((left, right) => (
          Number(right.choices.some((choice) => choice.checked))
          - Number(left.choices.some((choice) => choice.checked))
          || String(left.deviceName).localeCompare(String(right.deviceName), "ru")
        ));
        const optionNodes = [];
        deviceGroups.forEach((deviceGroup) => {
          const group = el("div", "entity-group device-card");
          setAttr(group, "data-device-group-id", deviceGroup.groupId);
          const header = el("div", "device-card-header");
          const thumb = el("div", "device-thumb");
          const fallback = el("span", "device-thumb-fallback");
          fallback.appendChild(svgIcon("device"));
          setAttr(fallback, "aria-hidden", "true");
          if (deviceGroup.imageUrl) {
            const image = el("img");
            image.src = deviceGroup.imageUrl;
            image.alt = "";
            setAttr(image, "loading", "lazy");
            setAttr(image, "decoding", "async");
            setAttr(image, "referrerpolicy", "no-referrer");
            fallback.hidden = true;
            image.addEventListener("error", () => {
              image.hidden = true;
              fallback.hidden = false;
            });
            thumb.appendChild(image);
          }
          thumb.appendChild(fallback);
          header.appendChild(thumb);
          const identity = el("div");
          identity.appendChild(el("strong", "device-card-title", deviceGroup.deviceName));
          const details = [deviceGroup.manufacturer, deviceGroup.model].filter(Boolean);
          if (details.length) {
            identity.appendChild(el("small", "device-card-meta", details.join(" · ")));
          }
          header.appendChild(identity);
          group.appendChild(header);
          const groupOptions = el("div", "device-card-options");
          deviceGroup.choices.sort((left, right) => (
            Number(right.checked) - Number(left.checked)
            || String(deviceTypes[left.type] || left.type)
              .localeCompare(String(deviceTypes[right.type] || right.type), "ru")
          )).forEach(({ candidate, type, checked, payloadOrder }) => {
            const checkbox = el("input");
            checkbox.type = "checkbox";
            checkbox.value = candidate.candidate_id;
            checkbox.checked = checked;
            checkbox.addEventListener("change", () => {
              if (checkbox.checked) {
                (fields.candidateBoxes[candidate.candidate_id] || []).forEach((peer) => {
                  if (peer !== checkbox) peer.checked = false;
                });
              }
              Object.values(fields.rooms).forEach((entry) => entry.updateSummary());
              this._wizardChanged();
            });
            const label = el("label", "device-option");
            label.appendChild(checkbox);
            const labelText = el("span", "entity-label");
            labelText.appendChild(el("strong", null, deviceTypes[type] || type));
            labelText.appendChild(el("small", null, candidate.name));
            label.appendChild(labelText);
            groupOptions.appendChild(label);
            const choice = {
              checkbox, candidateId: candidate.candidate_id, type, label, payloadOrder,
            };
            roomFields.devices.push(choice);
            fields.candidateBoxes[candidate.candidate_id] =
              fields.candidateBoxes[candidate.candidate_id] || [];
            fields.candidateBoxes[candidate.candidate_id].push(checkbox);
          });
          group.appendChild(groupOptions);
          groups.appendChild(group);
          optionNodes.push({
            node: group,
            searchText: normalizedText([
              deviceGroup.deviceName,
              deviceGroup.manufacturer,
              deviceGroup.model,
              ...deviceGroup.choices.flatMap(({ candidate, type }) => (
                [candidate.name, deviceTypes[type] || type]
              )),
            ].join(" ")),
          });
        });
        search.addEventListener("input", () => {
          const query = normalizedText(search.value);
          optionNodes.forEach((option) => {
            option.node.hidden = Boolean(query) && !option.searchText.includes(query);
          });
        });
        editor.appendChild(groups);
      };
      appendDevices("Устройства управления", ACTIVE_DEVICE_TYPES);
      appendDevices("Датчики", SENSOR_DEVICE_TYPES);

      const roomIssues = el("div", "wizard-issues");
      setAttr(roomIssues, "aria-live", "polite");
      editor.appendChild(roomIssues);
      issues.rooms[room.id] = roomIssues;
      roomFields.updateSummary = () => {
        const active = roomFields.devices.filter((device) => (
          device.checkbox.checked && ACTIVE_DEVICE_TYPES.has(device.type)
        )).length;
        const sensors = roomFields.devices.filter((device) => (
          device.checkbox.checked && SENSOR_DEVICE_TYPES.has(device.type)
        )).length;
        summaryText.textContent = include.checked
          ? `Выбрано: управление ${active}, датчики ${sensors}`
          : "Комната не включена; выбранные привязки сохранены в форме";
      };
      roomFields.setExpanded = (expanded, shouldFocus = false) => {
        roomFields.expanded = expanded;
        if (expanded) {
          roomFields.everExpanded = true;
          this._expandedWizardRooms.add(room.id);
        } else {
          this._expandedWizardRooms.delete(room.id);
        }
        editor.hidden = !expanded;
        expander.textContent = expanded ? "Свернуть" : "Настроить";
        setAttr(expander, "aria-expanded", expanded ? "true" : "false");
        if (shouldFocus) focusNode(expander);
      };
      expander.addEventListener("click", () => {
        roomFields.setExpanded(!roomFields.expanded);
      });
      roomFields.toggle = () => {
        const enabled = include.checked && canUseRoom;
        temperature.disabled = !enabled;
        humidity.disabled = !enabled;
        strategy.disabled = !enabled;
        roomFields.devices.forEach((device) => { device.checkbox.disabled = !enabled; });
      };
      include.addEventListener("change", () => {
        roomFields.toggle();
        roomFields.updateSummary();
        if (include.checked && !roomFields.everExpanded) roomFields.setExpanded(true);
        this._wizardChanged();
      });
      roomFields.toggle();
      fields.rooms[room.id] = roomFields;
      roomFields.updateSummary();
      roomFields.setExpanded(this._expandedWizardRooms.has(room.id));
      fields.controls.push(include, temperature, humidity, strategy);
      roomFields.devices.forEach((device) => fields.controls.push(device.checkbox));
      card.appendChild(block);
    });

    const globalIssues = el("div", "wizard-issues");
    const success = el("div", "wizard-success");
    issues.global = globalIssues;
    issues.success = success;
    card.appendChild(globalIssues);
    card.appendChild(success);
    const dirtyNotice = el("div", "unsaved", "Есть несохранённые изменения");
    dirtyNotice.hidden = !this._dirty.wizard;
    card.appendChild(dirtyNotice);

    const check = el("button", null, "Проверить контур");
    const save = el("button", null, "Сохранить контур");
    const cancel = el("button", "secondary", "Отмена");
    const saveHint = el(
      "div",
      "muted action-help",
      "Сохранение станет доступно после успешной проверки контура."
    );
    check.disabled = this._busy || (!editing && options.draft_creation_allowed !== true);
    save.disabled = true;
    save.title = "Сначала проверьте контур.";
    cancel.disabled = this._busy;
    check.addEventListener("click", () => this._checkWizard());
    save.addEventListener("click", () => this._saveWizard());
    cancel.addEventListener("click", () => this._cancelWizard());
    const actions = el("div", "actions");
    actions.appendChild(check);
    actions.appendChild(save);
    actions.appendChild(cancel);
    card.appendChild(actions);
    card.appendChild(saveHint);
    if (!editing && options.draft_creation_allowed !== true) {
      const missingRooms = !(options.rooms || []).length;
      card.appendChild(el(
        "div",
        "muted action-help",
        missingRooms
          ? "Создание недоступно: в Home Assistant не найдены зоны (комнаты)."
          : "Создание недоступно: нет доступных климатических устройств."
      ));
      const refresh = el(
        "button",
        "secondary",
        "Обновить комнаты и устройства"
      );
      refresh.disabled = this._busy || this._wizard.loading;
      refresh.addEventListener("click", () => this._refreshWizardOptions());
      card.appendChild(refresh);
    }
    this._wizardFields = fields;
    this._wizardIssues = issues;
    this._wizardButtons = {
      check,
      save,
      cancel,
      saveHint,
      dirtyNotice,
      editing,
      creationAllowed: options.draft_creation_allowed === true,
    };
    container.appendChild(card);
  }

  async _refreshWizardOptions() {
    if (this._busy || this._wizard.loading) return;
    if (
      this._dirty.wizard
      && !window.confirm("Обновить комнаты и устройства? Несохранённые изменения формы будут сброшены.")
    ) return;
    this._dirty.wizard = false;
    this._wizard.draft = null;
    this._wizard.validation = null;
    this._wizard.fingerprint = null;
    this._wizardFields = null;
    this._wizardIssues = null;
    this._wizardButtons = null;
    await this._loadWizardOptions(true);
  }

  _wizardChanged() {
    this._dirty.wizard = true;
    this._wizard.draft = null;
    this._wizard.validation = null;
    this._wizard.fingerprint = null;
    this._clearWizardIssues();
    if (this._wizardButtons) {
      this._wizardButtons.dirtyNotice.hidden = false;
      this._wizardButtons.save.disabled = true;
      this._wizardButtons.save.title = "Сначала проверьте контур.";
      this._wizardButtons.saveHint.textContent =
        "Сохранение станет доступно после успешной проверки контура.";
    }
    this._syncSectionVisibility();
  }

  _clearWizardIssues() {
    if (!this._wizardIssues) return;
    Object.values(this._wizardIssues.rooms).forEach((node) => { node.innerHTML = ""; });
    if (this._wizardFields) {
      Object.values(this._wizardFields.rooms).forEach((room) => {
        room.errorBadge.hidden = true;
      });
    }
    this._wizardIssues.global.innerHTML = "";
    this._wizardIssues.success.innerHTML = "";
  }

  _collectWizardPayload() {
    const fields = this._wizardFields;
    const options = this._wizard.options;
    if (!fields || !options) return { error: "Мастер контура ещё не готов." };
    const name = String(fields.name.value || "").trim();
    if (!name || name.length > 120) {
      return {
        error: "Введите название контура длиной не более 120 символов.",
        control: fields.name,
      };
    }
    if (!CONTOUR_MODE_ORDER.includes(fields.mode.value)) {
      return { error: "Выберите режим климатического контура.", control: fields.mode };
    }
    const setupRevision = this._wizard.setupRevision;
    if (!Number.isSafeInteger(setupRevision) || setupRevision < 0) {
      return { error: "Настройки контура изменились. Обновите страницу и повторите." };
    }
    const rooms = [];
    const selectedCandidates = new Set();
    for (const room of options.rooms || []) {
      const entry = fields.rooms[room.id];
      if (!entry || !entry.include.checked) continue;
      const rawTemperature = entry.temperature.value;
      const rawHumidity = entry.humidity.value;
      const temperature = Number(rawTemperature);
      const humidity = Number(rawHumidity);
      if (
        rawTemperature === "" || !Number.isFinite(temperature)
        || temperature < 18 || temperature > 28 || !Number.isInteger(temperature * 2)
      ) {
        return {
          error: `Проверьте температуру в комнате «${room.name || room.id}»: 18-28 °C, шаг 0,5 °C.`,
          roomId: room.id,
          control: entry.temperature,
        };
      }
      if (
        rawHumidity === "" || !Number.isFinite(humidity)
        || humidity < 30 || humidity > 70 || !Number.isInteger(humidity)
      ) {
        return {
          error: `Проверьте влажность в комнате «${room.name || room.id}»: 30-70 %, шаг 1 %.`,
          roomId: room.id,
          control: entry.humidity,
        };
      }
      if (!STRATEGY_ORDER.includes(entry.strategy.value)) {
        return {
          error: `Выберите стратегию для комнаты «${room.name || room.id}».`,
          roomId: room.id,
          control: entry.strategy,
        };
      }
      const devices = [];
      const selectedDevices = entry.devices
        .filter((choice) => choice.checkbox.checked)
        .sort((left, right) => left.payloadOrder - right.payloadOrder);
      for (const choice of selectedDevices) {
        if (selectedCandidates.has(choice.candidateId)) {
          return {
            error: "Одно устройство нельзя выбрать для нескольких комнат или типов.",
            roomId: room.id,
            control: choice.checkbox,
          };
        }
        selectedCandidates.add(choice.candidateId);
        devices.push({ candidate_id: choice.candidateId, type: choice.type });
      }
      if (!devices.length) {
        const firstDevice = entry.devices.find((choice) => ACTIVE_DEVICE_TYPES.has(choice.type))
          || entry.devices[0];
        return {
          error: `Выберите хотя бы одно устройство в комнате «${room.name || room.id}».`,
          roomId: room.id,
          control: firstDevice ? firstDevice.checkbox : entry.include,
        };
      }
      rooms.push({
        room_id: room.id,
        target_temperature: temperature,
        target_humidity: humidity,
        strategy: entry.strategy.value,
        devices,
      });
    }
    if (!rooms.length) {
      const firstRoom = (options.rooms || [])[0];
      return {
        error: "Выберите хотя бы одну комнату.",
        control: firstRoom && fields.rooms[firstRoom.id]
          ? fields.rooms[firstRoom.id].include : fields.name,
      };
    }
    return {
      payload: {
        snapshot_revision: options.snapshot_revision,
        setup_revision: setupRevision,
        name,
        mode: fields.mode.value,
        rooms,
      },
    };
  }

  _showWizardMessage(message, roomId = null, control = null) {
    this._clearWizardIssues();
    this._activateSection("climate");
    const room = roomId && this._wizardFields && this._wizardFields.rooms[roomId];
    if (room) {
      room.setExpanded(true);
      room.errorBadge.hidden = false;
      this._wizardIssues.rooms[roomId].appendChild(el("div", null, message));
    } else if (this._wizardIssues) {
      this._wizardIssues.global.appendChild(el("div", null, message));
    }
    focusNode(control || (room && room.expander));
  }

  _showWizardValidation(validation) {
    this._clearWizardIssues();
    let firstRoom = null;
    let firstControl = null;
    (validation.issues || []).forEach((issue) => {
      const room = issue.room_id && this._wizardFields.rooms[issue.room_id];
      if (room && issue.level !== "warning") {
        room.errorBadge.hidden = false;
        if (!firstRoom) firstRoom = room;
        if (!firstControl) {
          const candidate = issue.candidate_id
            ? room.devices.find((choice) => choice.candidateId === issue.candidate_id)
            : room.devices.find((choice) => ACTIVE_DEVICE_TYPES.has(choice.type));
          firstControl = candidate ? candidate.checkbox : room.include;
        }
      }
      const target = room ? this._wizardIssues.rooms[issue.room_id] : this._wizardIssues.global;
      target.appendChild(el(
        "div",
        issue.level === "warning" ? "issue-warning" : null,
        issue.message
      ));
    });
    const ready = validation.status === "ready" && validation.save_allowed === true;
    if (ready) {
      this._wizardIssues.success.appendChild(
        el("div", null, "Контур проверен. Можно сохранять.")
      );
    } else if (!(validation.issues || []).length) {
      this._wizardIssues.global.appendChild(
        el("div", null, "Контур не прошёл проверку. Проверьте выбранные значения.")
      );
    }
    this._wizardButtons.save.disabled = this._busy || !ready;
    this._wizardButtons.save.title = ready ? "" : "Сначала исправьте замечания проверки.";
    this._wizardButtons.saveHint.textContent = ready
      ? "Контур проверен: сохранение доступно."
      : "Сохранение станет доступно после успешной проверки контура.";
    if (!ready) {
      this._activateSection("climate");
      if (firstRoom) firstRoom.setExpanded(true);
      focusNode(firstControl || (firstRoom ? firstRoom.include : this._wizardButtons.check));
    }
  }

  _setWizardBusy(busy) {
    if (!this._wizardFields || !this._wizardButtons) return;
    this._wizardFields.name.disabled = busy;
    this._wizardFields.mode.disabled = busy;
    Object.values(this._wizardFields.rooms).forEach((room) => {
      room.include.disabled = busy || !room.canUseRoom;
      room.expander.disabled = busy;
      if (busy) {
        room.temperature.disabled = true;
        room.humidity.disabled = true;
        room.strategy.disabled = true;
        room.devices.forEach((device) => { device.checkbox.disabled = true; });
      } else {
        room.toggle();
      }
    });
    this._wizardButtons.check.disabled = busy
      || (!this._wizardButtons.editing && !this._wizardButtons.creationAllowed);
    const ready = this._wizard.validation
      && this._wizard.validation.status === "ready"
      && this._wizard.validation.save_allowed === true;
    this._wizardButtons.save.disabled = busy || !ready;
    this._wizardButtons.save.title = ready
      ? (busy ? "Дождитесь завершения операции." : "")
      : "Сначала проверьте контур.";
    this._wizardButtons.cancel.disabled = busy;
  }

  async _checkWizard() {
    if (this._busy) return;
    const collected = this._collectWizardPayload();
    if (collected.error) {
      this._showWizardMessage(collected.error, collected.roomId, collected.control);
      return;
    }
    if (!window.confirm("Проверить климатический контур с выбранными комнатами и устройствами?")) return;
    this._busy = true;
    this._dirty.wizard = true;
    this._notice = "";
    this._setWizardBusy(true);
    try {
      const draft = await this._hass.callApi("POST", DRAFT_API, collected.payload);
      const validation = await this._hass.callApi("POST", DRAFT_VALIDATE_API, draft);
      this._wizard.draft = draft;
      this._wizard.validation = validation;
      this._wizard.fingerprint = JSON.stringify(collected.payload);
      this._showWizardValidation(validation);
    } catch (error) {
      const policy = resolveApiError(error);
      if (policy.clientState === "stale") {
        await this._resetWizardAfterConflict(policy);
      } else {
        this._showWizardMessage(policy.safeMessage);
      }
    } finally {
      this._busy = false;
      this._setWizardBusy(false);
      this._render();
    }
  }

  async _saveWizard() {
    if (this._busy) return;
    const validation = this._wizard.validation;
    if (
      !this._wizard.draft || !validation
      || validation.status !== "ready" || validation.save_allowed !== true
    ) return;
    const collected = this._collectWizardPayload();
    if (collected.error || JSON.stringify(collected.payload) !== this._wizard.fingerprint) {
      this._wizardChanged();
      this._showWizardMessage("Форма изменилась после проверки. Проверьте контур ещё раз.");
      return;
    }
    if (!window.confirm(
      "Сохранить климатический контур? Настройка будет записана атомарно, команды устройствам не отправятся."
    )) return;
    this._busy = true;
    this._setWizardBusy(true);
    try {
      await this._hass.callApi("POST", DRAFT_SAVE_API, this._wizard.draft);
      this._dirty.wizard = false;
      this._wizard.open = false;
      this._expandedWizardRooms.clear();
      this._wizard.setupRevision = null;
      this._wizard.draft = null;
      this._wizard.validation = null;
      this._wizard.fingerprint = null;
      this._wizard.options = null;
      this._wizardFields = null;
      this._wizardIssues = null;
      this._wizardButtons = null;
      this._notice = "Контур сохранён. Команды устройствам не отправлялись.";
      this._error = false;
      await this._load();
      await this._loadWizardOptions(true);
    } catch (error) {
      const policy = resolveApiError(error);
      if (policy.clientState === "stale") {
        await this._resetWizardAfterConflict(policy);
      } else {
        this._showWizardMessage(policy.safeMessage);
      }
    } finally {
      this._busy = false;
      this._setWizardBusy(false);
      this._render();
    }
  }

  async _resetWizardAfterConflict(policy) {
    this._dirty.wizard = false;
    this._wizard.open = false;
    this._expandedWizardRooms.clear();
    this._wizard.setupRevision = null;
    this._wizard.options = null;
    this._wizard.optionsError = false;
    this._wizard.draft = null;
    this._wizard.validation = null;
    this._wizard.fingerprint = null;
    this._wizardFields = null;
    this._wizardIssues = null;
    this._wizardButtons = null;
    this._notice = policy && policy.safeMessage ? policy.safeMessage : apiErrorMessage(null);
    await this._load();
  }

  _renderContour(container, snapshot, setup) {
    container.innerHTML = "";
    if (!setup && !snapshot) return;
    container.appendChild(el("h2", null, "Контур"));
    container.appendChild(el(
      "div",
      "section-intro",
      "Состав комнат, цели и привязки климатических устройств. Сохранение не отправляет команды устройствам."
    ));
    this._renderContourWizard(container, setup);
    if (this._wizard.open || (setup && setup.status === "not_configured")) return;
    if (!snapshot) return;
    const contours = (snapshot.contours || []).filter((item) => item.kind === "climate");
    if (!contours.length) return;
    contours.forEach((contour) => {
      const card = el("div", "card contour-state-card");
      const head = el("div", "contour-card-head");
      head.appendChild(el("h3", null, contour.name));
      const healthy = ["normal", "ready", "active"].includes(contour.status);
      head.appendChild(el(
        "span",
        `status-badge ${healthy ? "is-ready" : "is-attention"}`,
        healthy ? "Система в норме" : "Нужно внимание"
      ));
      card.appendChild(head);
      const facts = el("div", "contour-facts");
      this._row(facts, "Статус", this._contourStatusName(contour.status));
      this._row(facts, "Режим", this._contourModeName(contour.mode));
      if (contour.schedule && contour.schedule.enabled) {
        const next = contour.schedule.next_profile
          ? `${this._profileName(contour.schedule.next_profile)} · ${contour.schedule.next_change_at || ""}`
          : "Расписание включено";
        this._row(facts, "Расписание", next);
      }
      card.appendChild(facts);
      if (Array.isArray(contour.reasons) && contour.reasons.length) {
        const reasons = el("div", "reasons");
        contour.reasons.forEach((reason) => {
          reasons.appendChild(el("span", "chip", this._names("contour_reasons", reason)));
        });
        card.appendChild(reasons);
      }

      const execution = contour.execution || {};
      const apply = execution.settings_apply || {};
      const applyButton = el("button", null, "Применить сохранённые настройки");
      applyButton.disabled = this._busy || apply.available !== true;
      applyButton.addEventListener("click", () => {
        this._post(
          `${PANEL_API}/apply`,
          {
            request_id: requestId("panel-apply"),
            contour_id: contour.id,
            confirm: true,
          },
          "Применить сохранённые настройки климата для всех комнат контура?"
        );
      });
      const cardActions = el("div", "actions contour-card-actions");
      cardActions.appendChild(applyButton);
      card.appendChild(cardActions);
      container.appendChild(card);

      const temporary = execution.temporary_temperature || {};
      const roomGrid = el("div", "contour-room-grid");
      (contour.rooms || []).forEach((room) => {
        const block = el("article", "card contour-room-card");
        const roomHead = el("div", "contour-room-head");
        roomHead.appendChild(el("h3", null, room.name || room.id));
        const temporaryActive = room.temporary_temperature && room.temporary_temperature.active;
        roomHead.appendChild(el(
          "span",
          `status-badge ${temporaryActive ? "is-attention" : ""}`,
          temporaryActive ? "Временная цель" : "По расписанию"
        ));
        block.appendChild(roomHead);
        const targets = room.targets || {};
        const meta = el("p", "muted contour-room-meta");
        const target = typeof targets.temperature === "number" ? `${targets.temperature.toFixed(1)} °C` : "нет цели";
        const devices = Array.isArray(room.devices) ? room.devices.length : (room.device_count || 0);
        meta.textContent = `Цель профиля: ${target} · Устройств: ${devices}`;
        block.appendChild(meta);
        const controls = el("div", "contour-room-controls");
        const input = el("input");
        input.type = "number";
        input.min = temporary.minimum;
        input.max = temporary.maximum;
        input.step = temporary.step;
        const current = room.temporary_temperature && room.temporary_temperature.active
          ? room.temporary_temperature.temperature
          : room.targets && room.targets.temperature;
        input.value = current;
        setAttr(input, "aria-label", `Временная температура, ${room.name || room.id}`);
        controls.appendChild(input);
        const setButton = el("button", "secondary", "Временная температура");
        setButton.disabled = this._busy || !room.temporary_temperature || room.temporary_temperature.available !== true;
        setButton.addEventListener("click", () => {
          this._post(
            `${PANEL_API}/temporary-temperature`,
            {
              request_id: requestId("panel-temp"),
              contour_id: contour.id,
              room_id: room.id,
              action: "set",
              target_temperature: Number(input.value),
              confirm: true,
            },
            `Установить временную температуру ${input.value} °C в комнате «${room.name || room.id}» до следующей границы расписания?`
          );
        });
        controls.appendChild(setButton);
        if (temporaryActive) {
          const clearButton = el("button", "secondary", "Вернуться к расписанию");
          clearButton.disabled = this._busy;
          clearButton.addEventListener("click", () => {
            this._post(
              `${PANEL_API}/temporary-temperature`,
              {
                request_id: requestId("panel-temp"),
                contour_id: contour.id,
                room_id: room.id,
                action: "clear",
                target_temperature: null,
                confirm: true,
              },
              `Вернуть комнату «${room.name || room.id}» к расписанию?`
            );
          });
          controls.appendChild(clearButton);
          block.appendChild(
            el("div", "muted", `Действует временная температура ${room.temporary_temperature.temperature} °C`)
          );
        }
        block.appendChild(controls);
        roomGrid.appendChild(block);
      });
      container.appendChild(roomGrid);
    });
  }

  _renderProfiles(container, setup) {
    container.innerHTML = "";
    if (!setup) {
      container.appendChild(el("h2", null, "Профили климата"));
      container.appendChild(el("div", "card empty-state muted", "Настройки профилей временно недоступны."));
      return;
    }
    container.appendChild(el("h2", null, "Профили климата"));
    container.appendChild(el(
      "div",
      "section-intro",
      "Настройте целевые значения каждой комнаты для дня и ночи."
    ));
    if (setup.status === "not_configured") {
      const card = el("div", "card empty-state");
      card.appendChild(
        el("div", "muted", "Климатический контур ещё не настроен. Сначала создайте его в разделе «Контур».")
      );
      const openWizard = el("button", "secondary", "Открыть мастер контура");
      openWizard.disabled = this._busy;
      openWizard.addEventListener("click", () => this._openWizard(setup));
      card.appendChild(openWizard);
      container.appendChild(card);
      return;
    }
    const editable = setup.editing_allowed === true;
    const strategies = (setup.display_names && setup.display_names.strategies) || {};
    const fields = {};
    const grid = el("div", "profile-room-grid");
    (setup.rooms || []).forEach((room) => {
      const card = el("article", "card profile-room");
      card.appendChild(el("h3", null, room.name || room.id));
      const columns = el("div", "profile-columns");
      const roomError = el("div", "field-error");
      fields[room.id] = { error: roomError };
      ["day", "night"].forEach((profile) => {
        const values = (room.profiles && room.profiles[profile]) || {};
        const title = (setup.display_names && setup.display_names.profiles
          && setup.display_names.profiles[profile]) || this._profileName(profile);
        const profileBlock = el("div", `profile-block is-${profile}`);
        const profileHead = el("div", "profile-block-head");
        const profileIcon = el("span", "profile-icon");
        profileIcon.appendChild(svgIcon(profile === "day" ? "sun" : "moon"));
        profileHead.appendChild(profileIcon);
        const profileCopy = el("div");
        profileCopy.appendChild(el("h4", "profile-block-title", title));
        profileCopy.appendChild(el("p", "muted", profile === "day" ? "Активный комфорт" : "Спокойный сон"));
        profileHead.appendChild(profileCopy);
        profileBlock.appendChild(profileHead);
        const temperature = numberField(
          values.target_temperature, 18, 28, 0.5,
          () => this._markDirty("profiles", dirtyNotice)
        );
        const humidity = numberField(
          values.target_humidity, 30, 70, 1,
          () => this._markDirty("profiles", dirtyNotice)
        );
        const strategy = selectField(
          STRATEGY_ORDER.map((code) => ({ value: code, label: strategies[code] || code })),
          values.strategy,
          () => this._markDirty("profiles", dirtyNotice)
        );
        temperature.disabled = !editable;
        humidity.disabled = !editable;
        strategy.disabled = !editable;
        const tempRow = el("label", "form-field", "Температура, °C");
        tempRow.appendChild(temperature);
        profileBlock.appendChild(tempRow);
        profileBlock.appendChild(el("div", "muted field-help", "18–28 °C, шаг 0,5 °C."));
        const humidityRow = el("label", "form-field", "Влажность, %");
        humidityRow.appendChild(humidity);
        profileBlock.appendChild(humidityRow);
        profileBlock.appendChild(el("div", "muted field-help", "30–70 %, шаг 1 %."));
        const strategyRow = el("label", "form-field", "Стратегия");
        strategyRow.appendChild(strategy);
        profileBlock.appendChild(strategyRow);
        fields[room.id][profile] = { temperature, humidity, strategy };
        columns.appendChild(profileBlock);
      });
      card.appendChild(columns);
      card.appendChild(roomError);
      grid.appendChild(card);
    });
    container.appendChild(grid);
    if (!editable) {
      container.appendChild(
        el("div", "muted", "Редактирование недоступно: данные устройств устарели или изменились.")
      );
    }
    const validationSummary = el("div", "field-error");
    setAttr(validationSummary, "role", "alert");
    container.appendChild(validationSummary);
    const dirtyNotice = el("div", "unsaved", "Есть несохранённые изменения");
    dirtyNotice.hidden = !this._dirty.profiles;
    container.appendChild(dirtyNotice);
    const saveButton = el("button", null, "Сохранить профили");
    saveButton.disabled = this._busy || !editable;
    saveButton.addEventListener("click", () => {
      const rooms = [];
      let firstInvalid = null;
      Object.values(fields).forEach((room) => { room.error.textContent = ""; });
      validationSummary.textContent = "";
      Object.keys(fields).forEach((roomId) => {
        const profiles = {};
        ["day", "night"].forEach((profile) => {
          const entry = fields[roomId][profile];
          const rawTemperature = entry.temperature.value;
          const rawHumidity = entry.humidity.value;
          const temperature = Number(rawTemperature);
          const humidity = Number(rawHumidity);
          if (
            rawTemperature === "" || rawHumidity === ""
            || !Number.isFinite(temperature) || temperature < 18 || temperature > 28
            || !Number.isInteger(temperature * 2)
            || !Number.isFinite(humidity) || humidity < 30 || humidity > 70
            || !Number.isInteger(humidity)
          ) {
            fields[roomId].error.textContent =
              "Проверьте температуру (18–28 °C, шаг 0,5) и влажность (30–70 %, шаг 1).";
            if (!firstInvalid) {
              firstInvalid = rawTemperature === "" || !Number.isFinite(temperature)
                || temperature < 18 || temperature > 28 || !Number.isInteger(temperature * 2)
                ? entry.temperature : entry.humidity;
            }
          }
          profiles[profile] = {
            target_temperature: temperature,
            target_humidity: Math.round(humidity),
            strategy: entry.strategy.value,
          };
        });
        rooms.push({ room_id: roomId, profiles });
      });
      if (firstInvalid) {
        validationSummary.textContent = "Исправьте отмеченные значения перед сохранением.";
        this._activateSection("climate");
        focusNode(firstInvalid);
        return;
      }
      this._save(
        "profiles",
        PROFILES_API,
        {
          contract: PROFILE_CONTRACT,
          setup_revision: setup.setup_revision,
          rooms,
        },
        "Сохранить профили «День» и «Ночь» для всех комнат?",
        "Профили сохранены."
      );
    });
    const actions = el("div", "actions");
    actions.appendChild(saveButton);
    container.appendChild(actions);
  }

  _renderSchedule(container, settings) {
    container.innerHTML = "";
    const setup = settings.setup;
    container.appendChild(el("h2", null, "Расписание"));
    container.appendChild(el(
      "div",
      "section-intro",
      "Границы дневного и ночного профилей и автоматическое переключение."
    ));
    if (!setup || setup.status === "not_configured") {
      container.appendChild(
        el("div", "card empty-state muted", "Расписание станет доступно после настройки контура.")
      );
      return;
    }
    const card = el("div", "card schedule-card");
    const schedule = setup.schedule || {};
    const managed = settings.mode && settings.mode.mode === "managed";
    const enabledBox = el("input");
    enabledBox.type = "checkbox";
    enabledBox.checked = schedule.enabled === true;
    enabledBox.disabled = this._busy || !managed;
    const enabledLabel = el("label", "checkbox-field");
    enabledLabel.appendChild(enabledBox);
    enabledLabel.appendChild(el(
      "span",
      null,
      "Автоматическое переключение профилей (в управляемом режиме устройствам отправляются команды)"
    ));
    card.appendChild(enabledLabel);
    if (!managed) {
      card.appendChild(
        el("div", "muted", "Включение расписания доступно после перевода климата в управляемый режим.")
      );
    }
    const dayStart = el("input");
    dayStart.type = "time";
    dayStart.value = schedule.day_start || "07:00";
    const dayRow = el("label", "form-field", "Начало дня");
    dayRow.appendChild(dayStart);
    const nightStart = el("input");
    nightStart.type = "time";
    nightStart.value = schedule.night_start || "23:00";
    const nightRow = el("label", "form-field", "Начало ночи");
    nightRow.appendChild(nightStart);
    const timeGrid = el("div", "schedule-time-grid");
    timeGrid.appendChild(dayRow);
    timeGrid.appendChild(nightRow);
    card.appendChild(timeGrid);
    const validationError = el("div", "field-error");
    setAttr(validationError, "role", "alert");
    card.appendChild(validationError);
    const dirtyNotice = el("div", "unsaved", "Есть несохранённые изменения");
    dirtyNotice.hidden = !this._dirty.schedule;
    card.appendChild(dirtyNotice);
    enabledBox.addEventListener("change", () => this._markDirty("schedule", dirtyNotice));
    dayStart.addEventListener("input", () => this._markDirty("schedule", dirtyNotice));
    nightStart.addEventListener("input", () => this._markDirty("schedule", dirtyNotice));
    const saveButton = el("button", null, "Сохранить расписание");
    saveButton.disabled = this._busy;
    saveButton.addEventListener("click", () => {
      const day = dayStart.value;
      const night = nightStart.value;
      if (!TIME_PATTERN.test(day) || !TIME_PATTERN.test(night) || day === night) {
        validationError.textContent =
          "Проверьте время: формат ЧЧ:ММ, начала дня и ночи должны отличаться.";
        this._activateSection("climate");
        focusNode(!TIME_PATTERN.test(day) || day === night ? dayStart : nightStart);
        return;
      }
      const enabled = enabledBox.checked === true;
      this._save(
        "schedule",
        SCHEDULE_API,
        {
          contract: SCHEDULE_CONTRACT,
          setup_revision: setup.setup_revision,
          schedule: { enabled, day_start: day, night_start: night },
          confirm_automatic_application: enabled,
        },
        enabled
          ? "Включить автоматическое переключение профилей по расписанию? В управляемом режиме устройствам будут отправляться команды."
          : "Сохранить расписание?",
        enabled ? "Расписание сохранено и включено." : "Расписание сохранено."
      );
    });
    const actions = el("div", "actions schedule-actions");
    actions.appendChild(saveButton);
    card.appendChild(actions);
    container.appendChild(card);
  }

  _signalCandidateType(candidate, signalKind) {
    const deviceClassLabels = {
      temperature: "Датчик температуры",
      motion: "Датчик движения",
      occupancy: "Датчик присутствия",
      presence: "Датчик присутствия",
      window: "Датчик окна",
      door: "Датчик двери",
      opening: "Датчик открытия",
      garage_door: "Датчик ворот",
      heat: "Датчик нагрева",
      running: "Датчик работы",
      power: "Датчик питания",
    };
    if (signalKind === "presence" && isAwayModeCandidate(candidate)) return AWAY_MODE_TYPE;
    if (candidate.missing && signalKind === "outdoor_temperature") {
      return "Ранее выбранное — Ранее выбранный источник";
    }
    if (candidate.domain === "weather") return "Погодный сервис";
    if (candidate.domain === "person") return "Члены дома (геолокация)";
    if (candidate.domain === "device_tracker") return "Телефоны и трекеры (геолокация)";
    if (candidate.domain === "switch") return "Выключатель";
    if (candidate.domain === "input_boolean") return "Логический переключатель";
    if (signalKind === "central_heating" && candidate.domain === "sensor") {
      return "Температура батареи / трубы";
    }
    if (deviceClassLabels[candidate.device_class]) {
      return deviceClassLabels[candidate.device_class];
    }
    if (signalKind === "outdoor_temperature") return "Датчик температуры";
    return "Другое устройство";
  }

  _candidateWithCurrent(candidates, current) {
    const result = Array.from(candidates || []);
    if (current && !result.some((candidate) => candidate.entity_id === current)) {
      result.push({
        entity_id: current,
        name: current,
        available: false,
        domain: String(current).split(".", 1)[0],
        room_id: "",
        missing: true,
      });
    }
    return result;
  }

  _signalCandidatesForPicker(candidates, current, signalKind) {
    const grouped = new Map();
    this._candidateWithCurrent(candidates, current).filter((candidate) => {
      const identity = normalizedText([
        candidate.name, candidate.entity_id, candidate.device_name, candidate.room_name,
      ].join(" "));
      if (signalKind === "outdoor_temperature") {
        if (candidate.missing || candidate.domain === "weather") return true;
        return candidate.domain === "sensor"
          && candidate.device_class === "temperature"
          && OUTDOOR_IDENTITY_PATTERN.test(identity);
      }
      if (signalKind === "presence") {
        return ["person", "device_tracker"].includes(candidate.domain)
          || (candidate.domain === "binary_sensor" && isAwayModeCandidate(candidate));
      }
      if (signalKind !== "central_heating") return true;
      return isCentralHeatingCandidate(candidate, identity);
    }).forEach((candidate) => {
      const key = candidate.device_group_id
        ? `${candidate.device_group_id}:${signalKind}` : candidate.entity_id;
      const existing = grouped.get(key);
      if (!existing || this._signalCandidateRank(candidate, current, signalKind)
          > this._signalCandidateRank(existing, current, signalKind)) {
        grouped.set(key, candidate);
      }
    });
    return [...grouped.values()];
  }

  _signalCandidateRank(candidate, current, signalKind) {
    const classRank = {
      presence: { presence: 60, occupancy: 50, motion: 30 },
      room_presence: { presence: 60, occupancy: 50, motion: 30 },
      window: { window: 60, opening: 50, door: 40, garage_door: 30 },
      central_heating: { heat: 60, running: 50, power: 40 },
    };
    const entityId = String(candidate.entity_id || "");
    return Number(entityId === current) * 10000
      + Number(candidate.available !== false) * 1000
      + ((classRank[signalKind] || {})[candidate.device_class] || 0)
      + Number(signalKind === "outdoor_temperature"
        && /(?:external|outdoor)_temperature/.test(entityId)) * 80
      - Math.min(entityId.length, 255) / 1000;
  }

  _signalCandidateDisplayName(candidate, peers = []) { return signalCandidateDisplayName(candidate, peers, normalizedText); }

  _signalCandidateExplanation(candidate, signalKind) {
    if (!candidate) {
      const disabledEffects = {
        outdoor_temperature: "Погодная блокировка отопления работать не будет.",
        presence: "Hausman Hub не сможет автоматически определить, дома ли кто-нибудь.",
        central_heating: "Состояние центрального отопления останется неизвестным.",
        window: "Состояние окна не будет учитываться для этой комнаты.",
      };
      return disabledEffects[signalKind] || "Этот сигнал не будет использоваться.";
    }
    if (candidate.missing) {
      return signalKind === "outdoor_temperature"
        ? "Выбранное ранее значение не подходит для наружной температуры или сейчас недоступно. Выберите другой источник."
        : "Ранее выбранный источник сейчас недоступен.";
    }
    if (signalKind === "presence" && isAwayModeCandidate(candidate)) return AWAY_MODE_EXPLANATION;
    if (candidate.available === false) return "Источник сейчас недоступен и может не обновлять состояние.";
    if (candidate.domain === "weather") return "Текущая температура из погодной интеграции Home Assistant.";
    if (candidate.domain === "person") return "профиль пользователя: дома / не дома. Это общий статус всего дома.";
    if (candidate.domain === "device_tracker") return "Телефон или трекер передаёт геолокационный статус «дома / не дома».";
    if (signalKind === "outdoor_temperature") return "Используется только показание температуры наружного воздуха.";
    if (signalKind === "central_heating" && candidate.domain === "sensor") {
      return "По температуре батареи или трубы Hausman Hub определяет, идёт ли сейчас подача тепла.";
    }
    if (signalKind === "central_heating") return "Используется прямое состояние «работает / не работает».";
    if (signalKind === "window") return "Используется состояние «открыто / закрыто» этой комнаты.";
    return "Физический датчик сообщает о движении или присутствии.";
  }

  _singleChoicePicker({
    title, helper, purpose, recommendation, candidates, current, signalKind, onChange,
    groupByRoom = true, pickerId = "",
  }) {
    const put = (parent, ...children) => {
      children.filter(Boolean).forEach((child) => parent.appendChild(child));
      return parent;
    };
    const fieldset = el("fieldset", "signal-picker");
    put(fieldset, el("legend", null, title), helper && el("div", "muted signal-picker-help", helper));
    if (purpose || recommendation) {
      const guide = el("div", "signal-picker-guide");
      [["Зачем это нужно", purpose], ["Как выбрать", recommendation]].forEach(([label, copy]) => {
        if (copy) put(guide, put(el("div"), el("strong", null, label), el("span", null, copy)));
      });
      put(fieldset, guide);
    }
    let selectedValue = current || "";
    const visible = this._signalCandidatesForPicker(candidates, current, signalKind);
    if (!visible.some((candidate) => candidate.entity_id === selectedValue)) selectedValue = "";
    const selectedCandidate = () => visible.find(({ entity_id }) => entity_id === selectedValue) || null;
    const currentCard = el("div", "signal-current");
    const currentCopy = el("div", "signal-current-copy");
    const currentName = el("strong");
    const currentMeta = el("small");
    const currentStatus = el("span", "status-badge");
    put(currentCopy, el("span", "signal-current-label", "Сейчас выбрано"), currentName, currentMeta);
    put(currentCard, currentCopy, currentStatus);
    const refresh = () => {
      const candidate = selectedCandidate();
      currentName.textContent = this._signalCandidateDisplayName(candidate, visible);
      currentMeta.textContent = this._signalCandidateExplanation(candidate, signalKind);
      currentStatus.textContent = candidate ? "Выбрано" : "Не настроено";
      currentStatus.className = `status-badge ${candidate ? "is-ready" : "is-attention"}`;
    };
    refresh();
    put(fieldset, currentCard);
    const chooser = el("details", "signal-chooser");
    chooser.open = Boolean(pickerId && this._openSignalPickers.has(pickerId));
    const chooserSummary = el("summary", "signal-chooser-summary");
    const chooserTitle = el("strong", null, selectedValue ? "Изменить источник" : "Выбрать источник");
    const last = visible.length % 10, lastTwo = visible.length % 100;
    const variantWord = last === 1 && lastTwo !== 11 ? "вариант"
      : last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14) ? "варианта" : "вариантов";
    put(chooserSummary, chooserTitle, el("span", "muted", `${visible.length} ${variantWord}`));
    put(chooser, chooserSummary);
    if (pickerId) chooser.addEventListener("toggle", () => {
      this._openSignalPickers[chooser.open ? "add" : "delete"](pickerId);
    });
    const search = el("input", "entity-search");
    Object.assign(search, { type: "search", placeholder: "Найти подходящее устройство" });
    setAttr(search, "aria-label", `Поиск: ${title.toLocaleLowerCase("ru")}`);
    const list = el("div", "signal-picker-list");
    put(chooser, search, list);
    put(fieldset, chooser);
    const radioName = requestId("signal");
    const radios = [], optionNodes = [];
    const groups = new Map();
    const addRadio = (container, candidate, value, label, meta) => {
      const radio = el("input");
      Object.assign(radio, { type: "radio", name: radioName, value, checked: value === selectedValue });
      const option = el("label", "signal-option");
      if (radio.checked) option.className += " is-selected";
      put(option, radio);
      let hasThumb = false;
      if (candidate && candidate.image_url
          && ZIGBEE2MQTT_IMAGE_PATTERN.test(candidate.image_url)) {
        hasThumb = true;
        option.className += " has-thumb";
        const thumb = el("span", "signal-option-thumb");
        const image = el("img");
        Object.assign(image, { src: candidate.image_url, alt: "" });
        setAttr(image, "loading", "lazy");
        setAttr(image, "decoding", "async");
        setAttr(image, "referrerpolicy", "no-referrer");
        image.addEventListener("error", () => { thumb.hidden = true; });
        put(option, put(thumb, image));
      }
      const identity = el("span", "entity-label");
      put(identity, el("strong", null, label), meta && el("small", null, meta));
      put(option, identity);
      radio.addEventListener("change", () => {
        if (!radio.checked) return;
        radios.forEach((peer) => {
          peer.radio.checked = peer.radio === radio;
          peer.option.className = `signal-option${peer.hasThumb ? " has-thumb" : ""}${peer.radio.checked ? " is-selected" : ""}`;
        });
        selectedValue = radio.value;
        chooserTitle.textContent = selectedValue ? "Изменить источник" : "Выбрать источник";
        refresh();
        onChange(selectedValue);
      });
      put(container, option);
      const item = { radio, option, hasThumb };
      radios.push(item);
      if (candidate) {
        item.node = option;
        item.searchText = normalizedText([
          candidate.name, candidate.entity_id, candidate.room_name, candidate.device_name,
          candidate.manufacturer, candidate.model, this._signalCandidateType(candidate, signalKind),
        ].join(" "));
        optionNodes.push(item);
      }
    };
    const noneGroup = el("div", "signal-type-options");
    addRadio(noneGroup, null, "", "Не привязано — не использовать", this._signalCandidateExplanation(null, signalKind));
    put(list, noneGroup);
    visible.sort((a, b) => String(a.room_name || "").localeCompare(String(b.room_name || ""), "ru")
      || String(a.name).localeCompare(String(b.name), "ru")).forEach((candidate) => {
        const roomLabel = candidate.missing ? "Ранее выбранное"
          : candidate.domain === "weather" ? "Погодные сервисы"
          : signalKind === "outdoor_temperature" ? "Уличные датчики"
          : ["person", "device_tracker"].includes(candidate.domain) ? "Присутствие дома"
          : candidate.room_name || candidate.room_id || "Без комнаты";
        const roomKey = groupByRoom ? roomLabel : "room";
        if (!groups.has(roomKey)) {
          const root = el("section", "signal-room-group");
          const options = el("div", "signal-type-options");
          put(root, groupByRoom && el("h4", null, roomLabel), options);
          put(list, root);
          groups.set(roomKey, { root, options, items: [] });
        }
        const group = groups.get(roomKey);
        const meta = `${this._signalCandidateType(candidate, signalKind)} · ${this._signalCandidateExplanation(candidate, signalKind)}`;
        addRadio(group.options, candidate, candidate.entity_id, this._signalCandidateDisplayName(candidate, visible), meta);
        group.items.push(optionNodes.at(-1));
      });
    if (!optionNodes.length) put(list, el("div", "muted", "Подходящих устройств пока не найдено."));
    search.addEventListener("input", () => {
      const query = normalizedText(search.value);
      optionNodes.forEach((item) => { item.node.hidden = Boolean(query) && !item.searchText.includes(query); });
      groups.forEach((group) => { group.root.hidden = group.items.every((item) => item.node.hidden); });
    });
    return { root: fieldset, value: () => selectedValue, radios };
  }

  _priorityChoicePicker(config) { return createPriorityChoicePicker(this, config, { el, setAttr }); }

  _renderHome(container, home) {
    container.innerHTML = "";
    container.appendChild(el("h2", null, "Сигналы дома"));
    container.appendChild(el(
      "div",
      "section-intro",
      "Общее присутствие управляет политикой «дома/нет дома». Оно не заменяет комнатные датчики присутствия."
    ));
    if (!home || !home.home) {
      container.appendChild(el("div", "card empty-state muted", "Сигналы дома временно недоступны."));
      return;
    }
    const card = el("div", "card home-signals-card");
    const values = home.home || {};
    const candidates = home.candidates || {};
    const pickers = {};
    HOME_SIGNAL_BINDINGS.forEach((binding) => {
      const picker = binding.kind === "outdoor_temperature"
        ? this._priorityChoicePicker({
          title: binding.title,
          helper: binding.helper,
          purpose: binding.purpose,
          recommendation: "Источники проверяются сверху вниз. Если основной недоступен или передаёт некорректное значение, Hausman Hub автоматически использует следующий.",
          candidates: candidates[binding.kind] || [],
          current: Array.isArray(values.outdoor_temperature_entity_ids)
            && values.outdoor_temperature_entity_ids.length
            ? values.outdoor_temperature_entity_ids
            : (values[binding.key] ? [values[binding.key]] : []),
          signalKind: binding.kind,
          pickerId: `home-${binding.key}`,
          onChange: () => this._markDirty("home", dirtyNotice),
        }) : this._singleChoicePicker({
        title: binding.title,
        helper: binding.helper,
        purpose: binding.purpose,
        recommendation: binding.recommendation,
        candidates: candidates[binding.kind] || [],
        current: values[binding.key],
        signalKind: binding.kind,
        pickerId: `home-${binding.key}`,
        onChange: () => {
          this._markDirty("home", dirtyNotice);
        },
        });
      card.appendChild(picker.root);
      pickers[binding.key] = picker;
    });
    const heatingThresholds = createHeatingTemperatureFields({
      onValue: values.central_heating_temperature_on,
      offValue: values.central_heating_temperature_off,
      onChange: () => {
        validationError.textContent = "";
        this._markDirty("home", dirtyNotice);
      },
    }, { el, numberField });
    card.appendChild(heatingThresholds.root);
    card.appendChild(el("h3", "threshold-heading", "Погодная блокировка отопления"));
    card.appendChild(el(
      "div",
      "muted threshold-intro",
      "Необязательная защита: Hausman Hub меняет разрешение на нагрев только после пересечения указанных порогов."
    ));
    const high = numberField(
      values.heating_lockout_high, -40, 60, 0.5,
      () => { validationError.textContent = ""; this._markDirty("home", dirtyNotice); }
    );
    const highRow = el("label", "form-field", "Блокировать нагрев теплее, °C");
    highRow.appendChild(high);
    const low = numberField(
      values.heating_lockout_low, -40, 60, 0.5,
      () => { validationError.textContent = ""; this._markDirty("home", dirtyNotice); }
    );
    const lowRow = el("label", "form-field", "Разрешать нагрев холоднее, °C");
    lowRow.appendChild(low);
    const thresholds = el("div", "home-threshold-grid");
    thresholds.appendChild(highRow);
    thresholds.appendChild(lowRow);
    card.appendChild(thresholds);
    card.appendChild(
      el("div", "muted", LOCKOUT_HELP)
    );
    card.appendChild(el("h3", "threshold-heading", "Защита кондиционеров от холода"));
    card.appendChild(el(
      "div", "muted threshold-intro",
      "При указанной наружной температуре и ниже Hausman Hub блокирует автоматический запуск и немедленно выключает работающий кондиционер, в том числе включённый вручную."
    ));
    const airConditionerMinimum = numberField(
      values.air_conditioner_minimum_outdoor_temperature === undefined
        ? -5 : values.air_conditioner_minimum_outdoor_temperature,
      -40, 60, 0.5,
      () => { validationError.textContent = ""; this._markDirty("home", dirtyNotice); }
    );
    const airConditionerMinimumRow = el(
      "label", "form-field", "Критический минимум на улице, °C"
    );
    airConditionerMinimumRow.appendChild(airConditionerMinimum);
    card.appendChild(airConditionerMinimumRow);
    card.appendChild(el(
      "div", "muted",
      "По умолчанию −5 °C. Уточните допустимую температуру работы в паспорте конкретной модели."
    ));
    card.appendChild(el("h3", "threshold-heading", "Межсезонье: отдых кондиционеров"));
    card.appendChild(el(
      "div", "muted threshold-intro",
      "Когда на улице уже не жарко, Hausman Hub не держит кондиционер в режиме поддержания, а выключает его. Кондиционер включится снова, только когда комната нагреется выше цели на указанный запас."
    ));
    const interseasonEnabled = el("input");
    interseasonEnabled.type = "checkbox";
    interseasonEnabled.checked = values.interseason_enabled === true;
    interseasonEnabled.addEventListener("change", () => {
      validationError.textContent = ""; this._markDirty("home", dirtyNotice);
    });
    const interseasonEnabledRow = el("label", "checkbox-field");
    interseasonEnabledRow.appendChild(interseasonEnabled);
    interseasonEnabledRow.appendChild(el("span", null, "Межсезонный режим включён"));
    card.appendChild(interseasonEnabledRow);
    const monthDayWire = (value) => {
      if (Array.isArray(value) && value.length === 2) {
        return `${String(value[0]).padStart(2, "0")}-${String(value[1]).padStart(2, "0")}`;
      }
      return typeof value === "string" ? value : "";
    };
    const interseasonOutdoorMax = numberField(
      values.interseason_outdoor_max_c === undefined ? 22 : values.interseason_outdoor_max_c,
      10, 30, 0.5,
      () => { validationError.textContent = ""; this._markDirty("home", dirtyNotice); }
    );
    const interseasonOutdoorMaxRow = el("label", "form-field", "Межсезонье на улице не теплее, °C");
    interseasonOutdoorMaxRow.appendChild(interseasonOutdoorMax);
    const interseasonGap = numberField(
      values.interseason_cooling_start_gap === undefined ? 2 : values.interseason_cooling_start_gap,
      1, 4, 0.1,
      () => { validationError.textContent = ""; this._markDirty("home", dirtyNotice); }
    );
    const interseasonGapRow = el("label", "form-field", "Включать охлаждение при превышении цели на, °C");
    interseasonGapRow.appendChild(interseasonGap);
    const interseasonGrid = el("div", "home-threshold-grid");
    interseasonGrid.appendChild(interseasonOutdoorMaxRow);
    interseasonGrid.appendChild(interseasonGapRow);
    card.appendChild(interseasonGrid);
    const interseasonWindowOff = el("input");
    interseasonWindowOff.type = "checkbox";
    interseasonWindowOff.checked = values.interseason_window_open_off !== false;
    interseasonWindowOff.addEventListener("change", () => {
      validationError.textContent = ""; this._markDirty("home", dirtyNotice);
    });
    const interseasonWindowOffRow = el("label", "checkbox-field");
    interseasonWindowOffRow.appendChild(interseasonWindowOff);
    interseasonWindowOffRow.appendChild(el("span", null, "Выключать кондиционер при открытом окне"));
    card.appendChild(interseasonWindowOffRow);
    const monthDayPattern = /^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$/;
    const interseasonDateStart = el("input");
    interseasonDateStart.type = "text";
    interseasonDateStart.placeholder = "ММ-ДД, например 08-15";
    interseasonDateStart.value = monthDayWire(values.interseason_date_start);
    interseasonDateStart.addEventListener("input", () => {
      validationError.textContent = ""; this._markDirty("home", dirtyNotice);
    });
    const interseasonDateStartRow = el("label", "form-field", "Начало сезона (необязательно)");
    interseasonDateStartRow.appendChild(interseasonDateStart);
    const interseasonDateEnd = el("input");
    interseasonDateEnd.type = "text";
    interseasonDateEnd.placeholder = "ММ-ДД, например 10-01";
    interseasonDateEnd.value = monthDayWire(values.interseason_date_end);
    interseasonDateEnd.addEventListener("input", () => {
      validationError.textContent = ""; this._markDirty("home", dirtyNotice);
    });
    const interseasonDateEndRow = el("label", "form-field", "Конец сезона (необязательно)");
    interseasonDateEndRow.appendChild(interseasonDateEnd);
    const interseasonDates = el("div", "home-threshold-grid");
    interseasonDates.appendChild(interseasonDateStartRow);
    interseasonDates.appendChild(interseasonDateEndRow);
    card.appendChild(interseasonDates);
    card.appendChild(el(
      "div", "muted",
      "Если даты заданы, режим работает только внутри этого окна. Пустые даты — режим зависит только от уличной температуры."
    ));
    const validationError = el("div", "field-error");
    setAttr(validationError, "role", "alert");
    card.appendChild(validationError);
    const dirtyNotice = el("div", "unsaved", "Есть несохранённые изменения");
    dirtyNotice.hidden = !this._dirty.home;
    card.appendChild(dirtyNotice);
    const saveButton = el("button", null, "Сохранить сигналы дома");
    saveButton.disabled = this._busy;
    saveButton.addEventListener("click", () => {
      const rawHigh = high.value;
      const rawLow = low.value;
      const highValue = Number(rawHigh);
      const lowValue = Number(rawLow);
      const heatingValues = heatingThresholds.values();
      const rawAirConditionerMinimum = airConditionerMinimum.value;
      const airConditionerMinimumValue = Number(rawAirConditionerMinimum);
      if (
        rawHigh === "" || rawLow === ""
        || !Number.isFinite(highValue) || !Number.isFinite(lowValue)
        || highValue < -40 || highValue > 60 || lowValue < -40 || lowValue > 60
        || lowValue >= highValue
        || !heatingThresholds.valid()
        || rawAirConditionerMinimum === ""
        || !Number.isFinite(airConditionerMinimumValue)
        || airConditionerMinimumValue < -40 || airConditionerMinimumValue > 60
      ) {
        validationError.textContent = "Проверьте пороги: нижний должен быть строго меньше верхнего.";
        this._activateSection("climate");
        focusNode(
          rawHigh === "" || !Number.isFinite(highValue) || highValue < -40 || highValue > 60
            ? high : low
        );
        return;
      }
      const rawInterseasonOutdoorMax = interseasonOutdoorMax.value;
      const interseasonOutdoorMaxValue = Number(rawInterseasonOutdoorMax);
      const rawInterseasonGap = interseasonGap.value;
      const interseasonGapValue = Number(rawInterseasonGap);
      const rawInterseasonDateStart = interseasonDateStart.value.trim();
      const rawInterseasonDateEnd = interseasonDateEnd.value.trim();
      const monthDayPattern = /^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$/;
      const interseasonDatesEmpty = rawInterseasonDateStart === "" && rawInterseasonDateEnd === "";
      const interseasonDatesValid = monthDayPattern.test(rawInterseasonDateStart)
        && monthDayPattern.test(rawInterseasonDateEnd);
      if (
        rawInterseasonOutdoorMax === ""
        || !Number.isFinite(interseasonOutdoorMaxValue)
        || interseasonOutdoorMaxValue < 5 || interseasonOutdoorMaxValue > 35
        || rawInterseasonGap === ""
        || !Number.isFinite(interseasonGapValue)
        || interseasonGapValue < 1 || interseasonGapValue > 4
        || (!interseasonDatesEmpty && !interseasonDatesValid)
      ) {
        validationError.textContent = "Проверьте межсезонье: улица 5-35 °C, запас 1-4 °C, даты ММ-ДД обе или ни одной.";
        this._activateSection("climate");
        focusNode(interseasonOutdoorMax);
        return;
      }
      const outdoorSources = pickers.outdoor_temperature_entity_id.value();
      this._save(
        "home",
        HOME_API,
        {
          outdoor_temperature_entity_id:
            outdoorSources[0] || null,
          outdoor_temperature_entity_ids: outdoorSources,
          presence_entity_id: pickers.presence_entity_id.value() || null,
          central_heating_entity_id:
            pickers.central_heating_entity_id.value() || null,
          central_heating_temperature_on: heatingValues.on,
          central_heating_temperature_off: heatingValues.off,
          heating_lockout_high: highValue,
          heating_lockout_low: lowValue,
          air_conditioner_minimum_outdoor_temperature:
            airConditionerMinimumValue,
          interseason_enabled: interseasonEnabled.checked,
          interseason_outdoor_max_c: interseasonOutdoorMaxValue,
          interseason_cooling_start_gap: interseasonGapValue,
          interseason_window_open_off: interseasonWindowOff.checked,
          interseason_date_start:
            interseasonDatesEmpty ? null : rawInterseasonDateStart,
          interseason_date_end:
            interseasonDatesEmpty ? null : rawInterseasonDateEnd,
        },
        "Сохранить привязки сигналов дома и пороги блокировки отопления?",
        "Сигналы дома сохранены."
      );
    });
    const actions = el("div", "actions climate-form-actions");
    actions.appendChild(saveButton);
    card.appendChild(actions);
    container.appendChild(card);
  }

  _renderWindows(container, windows) {
    container.innerHTML = "";
    container.appendChild(el("h2", null, "Сигналы комнат"));
    container.appendChild(el(
      "div",
      "section-intro",
      "Окно - одиночная привязка. Комнатное присутствие - набор датчиков и пока не меняет температуру мгновенно: для этого нужна отдельная политика присутствия."
    ));
    if (!windows) {
      container.appendChild(
        el("div", "card empty-state muted", "Сигналы комнат временно недоступны.")
      );
      return;
    }
    const rooms = windows.rooms || [];
    if (!rooms.length) {
      container.appendChild(
        el("div", "card empty-state muted", "Комнаты появятся здесь после настройки контура.")
      );
      return;
    }
    const presenceCandidates = (
      windows.presence_candidates || windows.candidates || []
    ).filter((item) => (
      ROOM_PRESENCE_DEVICE_CLASSES.has(item.device_class)
    ));
    const fields = {};
    const presenceBoxes = {};
    const dirtyNotice = el("div", "unsaved", "Есть несохранённые изменения");
    dirtyNotice.hidden = !this._dirty.windows;
    const grid = el("div", "room-card-grid room-signals-grid");
    rooms.forEach((room) => {
      const block = el("article", "card signal-room");
      block.appendChild(el("h3", null, room.name || room.id));
      const roomWindows = (windows.candidates || []).filter((candidate) => (
        candidate.room_id === room.id
        || candidate.entity_id === room.window_entity_id
      ));
      const windowPicker = this._singleChoicePicker({
        title: "Датчик окна",
        helper: "Показаны только датчики открытия, назначенные этой комнате.",
        purpose: "Открытое окно может временно ограничить климатическое управление в комнате.",
        recommendation: "Выберите физический датчик именно этого окна. Если датчика нет, оставьте источник непривязанным.",
        candidates: roomWindows,
        current: room.window_entity_id,
        signalKind: "window",
        groupByRoom: false,
        pickerId: `room-window-${room.id}`,
        onChange: () => {
          this._markDirty("windows", dirtyNotice);
        },
      });
      block.appendChild(windowPicker.root);
      block.appendChild(el("h4", null, "Датчики присутствия"));
      block.appendChild(
        el("div", "muted", "Можно выбрать несколько датчиков движения или присутствия; один датчик относится только к одной комнате.")
      );
      const selected = new Set(room.presence_entity_ids || []);
      const roomPresenceById = new Map(
        presenceCandidates
          .filter((candidate) => (
            candidate.room_id === room.id || selected.has(candidate.entity_id)
          ))
          .map((candidate) => [candidate.entity_id, candidate])
      );
      selected.forEach((entityId) => {
        if (!roomPresenceById.has(entityId)) {
          roomPresenceById.set(entityId, {
            entity_id: entityId,
            name: entityId,
            available: false,
            room_id: room.id,
            missing: true,
          });
        }
      });
      const boxes = [];
      const search = el("input", "entity-search");
      search.type = "search";
      search.placeholder = "Найти датчик присутствия";
      setAttr(search, "aria-label", `Поиск датчиков присутствия: ${room.name || room.id}`);
      block.appendChild(search);
      const groups = el("div", "entity-groups");
      const groupNodes = new Map();
      const optionNodes = [];
      const classNames = {
        motion: "Движение",
        occupancy: "Присутствие",
        presence: "Присутствие",
        other: "Шаблонные датчики",
      };
      Array.from(roomPresenceById.values())
        .sort((left, right) => (
          Number(selected.has(right.entity_id)) - Number(selected.has(left.entity_id))
          || String(left.name).localeCompare(String(right.name), "ru")
        ))
        .forEach((candidate) => {
        const category = ROOM_PRESENCE_DEVICE_CLASSES.has(candidate.device_class)
          ? candidate.device_class : "other";
        if (!groupNodes.has(category)) {
          const group = el("div", "entity-group");
          group.appendChild(el("h4", null, classNames[category]));
          groups.appendChild(group);
          groupNodes.set(category, group);
        }
        const checkbox = el("input");
        checkbox.type = "checkbox";
        checkbox.value = candidate.entity_id;
        checkbox.checked = selected.has(candidate.entity_id);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) {
            (presenceBoxes[candidate.entity_id] || []).forEach((peer) => {
              if (peer !== checkbox) peer.checked = false;
            });
          }
          this._markDirty("windows", dirtyNotice);
        });
        const label = el("label", "device-option");
        label.appendChild(checkbox);
        if (candidate.image_url
            && ZIGBEE2MQTT_IMAGE_PATTERN.test(candidate.image_url)) {
          const thumb = el("span", "signal-option-thumb");
          const image = el("img");
          image.src = candidate.image_url;
          image.alt = "";
          setAttr(image, "loading", "lazy");
          setAttr(image, "decoding", "async");
          setAttr(image, "referrerpolicy", "no-referrer");
          image.addEventListener("error", () => { thumb.hidden = true; });
          thumb.appendChild(image);
          label.appendChild(thumb);
        }
        const labelText = el("span", "entity-label");
        labelText.appendChild(el("strong", null, candidate.name || candidate.entity_id));
        labelText.appendChild(el("small", null, candidate.entity_id));
        label.appendChild(labelText);
        groupNodes.get(category).appendChild(label);
        optionNodes.push({
          node: label,
          searchText: normalizedText(`${candidate.name} ${candidate.entity_id}`),
        });
        boxes.push(checkbox);
        presenceBoxes[candidate.entity_id] = presenceBoxes[candidate.entity_id] || [];
        presenceBoxes[candidate.entity_id].push(checkbox);
      });
      if (!boxes.length) {
        block.appendChild(
          el("div", "muted", "Подходящие binary_sensor пока не найдены.")
        );
      } else {
        block.appendChild(groups);
      }
      search.addEventListener("input", () => {
        const query = normalizedText(search.value);
        optionNodes.forEach((option) => {
          option.node.hidden = Boolean(query) && !option.searchText.includes(query);
        });
      });
      grid.appendChild(block);
      fields[room.id] = {
        windowPicker,
        boxes,
        originalWindow: room.window_entity_id || "",
        originalPresence: Array.from(selected).sort(),
      };
    });
    container.appendChild(grid);
    const selectedPresence = (roomId) => fields[roomId].boxes
      .filter((checkbox) => checkbox.checked)
      .map((checkbox) => checkbox.value)
      .sort();
    const saveButton = el("button", null, "Сохранить сигналы комнат");
    saveButton.disabled = this._busy;
    saveButton.addEventListener("click", async () => {
      if (this._busy) return;
      const changed = Object.keys(fields).filter((roomId) => (
        fields[roomId].windowPicker.value() !== fields[roomId].originalWindow
        || JSON.stringify(selectedPresence(roomId))
          !== JSON.stringify(fields[roomId].originalPresence)
      ));
      if (!changed.length) {
        this._notice = "Сигналы комнат не изменились.";
        this._render();
        return;
      }
      if (!window.confirm(`Сохранить сигналы для комнат: ${changed.length}?`)) return;
      this._busy = true;
      saveButton.disabled = true;
      this._notice = "";
      this._render();
      let saveError = null;
      try {
        await this._hass.callApi("POST", WINDOWS_API, {
          rooms: changed.map((roomId) => ({
            room_id: roomId,
            window_entity_id: fields[roomId].windowPicker.value() || null,
            presence_entity_ids: selectedPresence(roomId),
          })),
        });
      } catch (error) {
        saveError = error;
      }
      this._dirty.windows = false;
      this._busy = false;
      this._notice = saveError
        ? apiErrorMessage(saveError)
        : "Сигналы комнат сохранены.";
      await this._load();
    });
    container.appendChild(dirtyNotice);
    const actions = el("div", "actions climate-form-actions");
    actions.appendChild(saveButton);
    container.appendChild(actions);
  }

  _row(card, label, value) {
    const row = el("div", "row");
    row.appendChild(el("span", null, label));
    row.appendChild(el("span", "value", value));
    card.appendChild(row);
  }

  _temp(value) {
    return typeof value === "number" ? `${value.toFixed(1)} °C` : "Нет данных";
  }

  _humidity(value) {
    return typeof value === "number" ? `${Math.round(value)} %` : "Нет данных";
  }

  _deviceIcon(device) {
    const domain = String(device && device.domain || "");
    const category = String(device && device.category || "");
    if (domain === "light" || category === "lighting") return "lightbulb";
    if (domain === "media_player" || category === "media") return "media";
    if (domain === "lock") return "lock";
    if (domain === "alarm_control_panel") return "alarm";
    if (domain === "camera") return "camera";
    if (category === "moisture") return "water";
    if (["smoke", "gas", "carbon_monoxide", "safety", "problem"].includes(category)) return "warning";
    if (["opening", "door", "window"].includes(category)) return "lock";
    if (["motion", "occupancy", "presence"].includes(category)) return "alarm";
    if (category === "security") return "shield";
    if (domain === "climate" || ["climate", "air"].includes(category)) return "thermometer";
    return "device";
  }

  _deviceCategoryName(device) {
    const names = {
      lighting: "Освещение", climate: "Климат", air: "Воздух", media: "Медиа",
      security: "Безопасность", moisture: "Датчик протечки", smoke: "Датчик дыма",
      gas: "Датчик газа", carbon_monoxide: "Датчик угарного газа", motion: "Датчик движения",
      occupancy: "Датчик присутствия", presence: "Датчик присутствия", opening: "Датчик открытия",
      door: "Дверь", window: "Окно", cover: "Шторы и ворота", appliance: "Техника", other: "Другое",
    };
    return names[device && device.category] || "Устройство";
  }

  _homeDevices(sectionId) {
    return renderHomeSection.homeDevices(this, sectionId, normalizedText);
  }

  _resolveIntercomAction(deviceId, catalog) {
    return resolveIntercomQuickAction(this._homeDevices("devices"), catalog, deviceId);
  }

  _catalogTargets(device) {
    const catalog = this._scenarios.catalog && Array.isArray(this._scenarios.catalog.devices)
      ? this._scenarios.catalog.devices : [];
    const entityIds = new Set([device.entityId].concat(
      Array.isArray(device.details) ? device.details.map((item) => item.entityId) : []
    ).filter(Boolean));
    const matrix = this._deviceFeatures && this._deviceFeatures.matrix;
    const deviceType = String(device && device.domain
      || String(device && device.entityId || "").split(".")[0] || "");
    return catalog.filter((target) => entityIds.has(target.entity_id))
      .map((target) => ({
        ...target,
        actions: filterCatalogActions(matrix, deviceType, target.actions),
      }))
      .filter((target) => target.actions.length);
  }

  _renderHomeSection(sectionId, container) {
    const dashboard = this._homeDashboard || null;
    const key = JSON.stringify({
      dashboard,
      climateRuntime: sectionId === "climate" ? this._climateRuntime : null,
      sectionId,
      meterStatus: this._energyMeter && this._energyMeter.submission
        ? [this._energyMeter.submission.status, this._energyMeter.submission.nextDate] : null,
      energy: sectionId === "energy" ? this._energyHistory : null,
      energyUi: sectionId === "energy" ? [this._energySettingsOpen, this._energySelectedDeviceId, this._energyDraft,
        this._energyDetailsOpen, this._energyModalView, this._energyFilter, this._energyMeter,
        this._energyMeterNotice, this._energyMeterError, this._energyMeterLoading, this._energyMeterSaving] : null,
      securityUi: sectionId === "security" ? this._securityTypeFilter : null,
      devicesUi: sectionId === "devices" ? this._deviceCategoryFilter : null,
      discoveryUi: sectionId === "devices" ? [this._deviceDiscovery, this._deviceDiscoveryPending,
        this._deviceDiscoveryMessages, this._deviceDiscoveryNotice, this._deviceDiscoveryError,
        this._deviceDiscoveryLoading, this._deviceDiscoveryAreaDrafts] : null,
    });
    if (container.childNodes && container.childNodes.length && this._sectionRenderKeys[sectionId] === key) return;
    this._sectionRenderKeys[sectionId] = key;
    renderHomeSection(this, sectionId, container, {
      el, svgIcon, normalizedText,
      setAttr,
      renderClimateOverview,
      renderLightingOverview,
      renderRoomsOverview,
      renderMediaOverview,
      renderSecurityOverview,
      renderDevicesOverview,
      sections: PANEL_SECTIONS,
      subtitles: SECTION_SUBTITLES,
    });
  }

  _renderEnergySection(container) {
    renderEnergySection(this, container, { el, svgIcon, setAttr });
  }

  async _saveEnergySettings() {
    if (this._energySettingsSaving || !this._energyDraft || !this._hass) return;
    this._energySettingsSaving = true;
    this._notice = "";
    this._render();
    try {
      await saveEnergySettings(this);
      this._energyDraft = null;
      this._energySettingsOpen = false;
      this._notice = "Настройка энергии сохранена в Home Assistant.";
      this._error = false;
      await this._load();
    } catch (error) {
      this._notice = apiErrorMessage(error);
    } finally {
      this._energySettingsSaving = false;
      this._render();
    }
  }

  _deviceCountWord(count) {
    return renderHomeSection.deviceCountWord(count);
  }

  _roomCountWord(count) {
    return renderHomeSection.roomCountWord(count);
  }

  _renderRoomInventory(container) {
    const rooms = Array.isArray(this._homeDashboard.rooms) ? this._homeDashboard.rooms : [];
    const devices = this._homeDevices("rooms");
    if (!rooms.length) {
      container.appendChild(el("div", "card empty-state", "Комнаты Home Assistant не найдены."));
      return;
    }
    const grid = el("div", "room-inventory-grid");
    rooms.forEach((room) => {
      const roomDevices = devices.filter((device) => device.roomId === room.id);
      const card = el("details", "card room-inventory-card");
      const openKey = `room:${room.id}`;
      card.open = this._openHomeCards.has(openKey);
      card.addEventListener("toggle", () => {
        if (card.open) this._openHomeCards.add(openKey);
        else this._openHomeCards.delete(openKey);
      });
      const summary = el("summary", "room-inventory-summary");
      const icon = el("span", "inventory-device-icon");
      icon.appendChild(svgIcon("rooms"));
      summary.appendChild(icon);
      const copy = el("span", "room-inventory-copy");
      copy.appendChild(el("strong", null, room.name));
      copy.appendChild(el("small", null, `${roomDevices.length} ${this._deviceCountWord(roomDevices.length)} · ${this._temp(room.temp)} · ${this._humidity(room.humidity)}`));
      summary.appendChild(copy);
      summary.appendChild(el("span", "room-inventory-open", "К устройствам"));
      card.appendChild(summary);
      const body = el("div", "room-inventory-body");
      if (roomDevices.length) {
        const deviceGrid = el("div", "inventory-device-grid");
        roomDevices.forEach((device) => deviceGrid.appendChild(this._deviceInventoryCard(device)));
        body.appendChild(deviceGrid);
      } else {
        body.appendChild(el("p", "muted", "В этой комнате пока нет физических устройств."));
      }
      card.appendChild(body);
      grid.appendChild(card);
    });
    container.appendChild(grid);
  }

  _renderAlarmSummary(container) {
    const alarms = Array.isArray(this._homeDashboard.alarms) ? this._homeDashboard.alarms : [];
    if (!alarms.length) return;
    const active = alarms.filter((alarm) => alarm.active === true);
    const card = el("div", `security-summary${active.length ? " has-alert" : ""}`);
    const icon = el("span", "home-section-icon");
    icon.appendChild(svgIcon("shield"));
    card.appendChild(icon);
    const copy = el("div");
    copy.appendChild(el("strong", null, active.length ? `Активных тревог: ${active.length}` : "Активных тревог нет"));
    copy.appendChild(el("span", "muted", `Под наблюдением: ${alarms.length}`));
    card.appendChild(copy);
    container.appendChild(card);
  }

  _deviceInventoryCard(device) {
    const mediaCard = renderMediaDeviceCard(this, device, { el, svgIcon, setAttr });
    if (mediaCard) return mediaCard;
    return renderPhysicalDeviceCard(this, device, { el, svgIcon, setAttr });
  }

  _deviceActionInitialValue(device, target, action) {
    const details = Array.isArray(device && device.details) ? device.details : [];
    const detail = details.find((item) => item.entityId === target.entity_id);
    const attributes = device && device.entityId === target.entity_id && device.attributes
      ? device.attributes : {};
    const numeric = (...values) => {
      const value = values.find((candidate) => (
        candidate !== null && candidate !== undefined && candidate !== ""
        && Number.isFinite(Number(candidate))
      ));
      return value === undefined ? null : Number(value);
    };
    if (action.action_id === "set_temperature") {
      return numeric(attributes.temperature, device && device.primaryValue, detail && detail.state);
    }
    if (action.action_id === "set_brightness") {
      return numeric(attributes.brightness, detail && detail.state);
    }
    if (action.action_id === "set_position") {
      return numeric(attributes.current_position, detail && detail.state);
    }
    if (action.action_id === "set_hvac_mode") {
      return String((detail && detail.state) || (device && device.state) || "").trim() || null;
    }
    if (action.action_id === "set_fan_mode") {
      return String(attributes.fan_mode || "").trim() || null;
    }
    if (action.action_id === "set_humidity") {
      return numeric(attributes.humidity, device && device.primaryValue, detail && detail.state);
    }
    return null;
  }

  _deviceTargetControls(target, device) {
    const row = el("div", "device-target-controls");
    row.appendChild(el("strong", "device-target-name", target.name || "Управление"));
    const actions = el("div", "device-action-list");
    (target.actions || []).forEach((action) => {
      const fields = Array.isArray(action.allowed_fields) ? action.allowed_fields : [];
      if (!fields.includes("value")) {
        const label=conciseDeviceActionLabel(action, target, device);
        const accessibleLabel=`${label}, ${device.name || target.name || "устройство"}`;
        const button = el("button", "secondary device-action", label);
        button.type = "button";
        setAttr(button, "aria-label", accessibleLabel);
        button.disabled = this._busy;
        button.addEventListener("click", (event) => {
          event.preventDefault();
          this._executeDeviceAction(target.target_id, action.action_id, null);
        });
        actions.appendChild(button);
        return;
      }
      const valueRow = el("label", "device-value-action");
      valueRow.appendChild(el("span", null, conciseDeviceActionLabel(action, target, device)));
      const input = el("input");
      const numericAction = ["set_temperature", "set_brightness", "set_position"]
        .includes(action.action_id);
      input.type = numericAction ? "number" : "text";
      if (numericAction) {
        input.min = action.action_id === "set_temperature" ? "10" : "0";
        input.max = action.action_id === "set_brightness" ? "255"
          : (action.action_id === "set_temperature" ? "35" : "100");
        input.step = action.action_id === "set_temperature" ? "0.5" : "1";
      }
      const initialValue = this._deviceActionInitialValue(device, target, action);
      input.value = initialValue === null ? "" : String(initialValue);
      input.placeholder = numericAction ? "Укажите значение" : "Укажите режим";
      valueRow.appendChild(input);
      const apply = el("button", "secondary", "Применить");
      apply.type = "button";
      const syncApplyState = () => {
        apply.disabled = this._busy || (numericAction
          ? !Number.isFinite(Number(input.value)) || input.value === ""
          : !String(input.value || "").trim());
      };
      syncApplyState();
      input.addEventListener("input", syncApplyState);
      apply.addEventListener("click", (event) => {
        event.preventDefault();
        const value = numericAction ? Number(input.value) : String(input.value).trim();
        this._executeDeviceAction(target.target_id, action.action_id, value);
      });
      valueRow.appendChild(apply);
      actions.appendChild(valueRow);
    });
    row.appendChild(actions);
    return row;
  }

  async _executeDeviceAction(targetId, actionId, value) {
    if (this._busy || !this._hass) return;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      const payload = { targetId, actionId };
      if (value !== null && value !== undefined) payload.value = value;
      const receipt = await this._hass.callApi("POST", DEVICE_ACTIONS_API, withCorrelationId(DEVICE_ACTIONS_API, payload));
      this._notice = this._receiptText(receipt);
      this._error = false;
    } catch (error) {
      this._notice = apiErrorMessage(error);
      this._error = false;
    } finally {
      this._busy = false;
      await this._load();
    }
  }

  _renderScenarios(container) {
    renderScenarioSection(this, container, {
      el, svgIcon, setAttr, scenariosApi: SCENARIOS_API, testApi: SCENARIOS_TEST_API,
      deleteApi: SCENARIOS_DELETE_API, runApi: SCENARIOS_RUN_API,
    });
  }

  async _scenarioTest(scenario) {
    if (this._busy) return;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      const result = await this._hass.callApi("POST", SCENARIOS_TEST_API, scenario);
      this._notice = result && result.ok ? `Сценарий "${scenario.title}" прошёл проверку.` : "Проверка сценария не пройдена.";
      this._error = false;
    } catch (error) {
      this._notice = apiErrorMessage(error);
    } finally {
      this._busy = false;
    }
    this._render();
  }

  _activateSettingsView(viewId) {
    if (!SETTINGS_VIEWS.some((view) => view.id === viewId)) return;
    const changed = this._activeSettingsView !== viewId;
    this._activeSettingsView = viewId;
    this._resetArmed = false;
    this._render();
    this._loadActiveNavigationView();
    if (changed && this._activeSection === "settings") writeNavigationRoute(this);
  }

  _settingsOverviewLink({ viewId, icon, title, description, value, status }) {
    const card = el("button", "settings-menu-card");
    card.type = "button";
    card.appendChild(svgIcon(icon, "settings-menu-icon"));
    const copy = el("span", "settings-menu-copy");
    copy.appendChild(el("strong", null, title));
    copy.appendChild(el("small", null, description));
    card.appendChild(copy);
    const meta = el("span", "settings-menu-meta");
    if (status) meta.appendChild(el("span", `settings-menu-status ${status.className || ""}`, status.label));
    meta.appendChild(el("b", null, value));
    meta.appendChild(el("span", "settings-menu-chevron", "›"));
    card.appendChild(meta);
    card.addEventListener("click", () => this._activateSettingsView(viewId));
    return card;
  }

  _renderSettingsOverview(container) {
    const setup = this._settings.setup || {};
    const rooms = Array.isArray(setup.rooms) ? setup.rooms : [];
    const roomCount = Number(setup.summary && setup.summary.room_count) || rooms.length;
    const deviceCount = Number(setup.summary && setup.summary.device_count)
      || rooms.reduce((sum, room) => sum + (Array.isArray(room.devices) ? room.devices.length : 0), 0);
    const configured = setup.status !== "not_configured";
    const grid = el("div", "settings-overview-grid");
    grid.appendChild(this._settingsOverviewLink({
      viewId: "rooms", icon: "rooms", title: "Комнаты и устройства",
      description: "Какие устройства относятся к комнатам и участвуют в управлении",
      value: configured ? `${roomCount} комнат · ${deviceCount} устройств` : "Требуется настройка",
      status: { className: configured ? "is-ready" : "is-warning", label: configured ? "Настроено" : "Внимание" },
    }));
    grid.appendChild(this._settingsOverviewLink({
      viewId: "connection", icon: "device", title: "Подключение",
      description: "Откуда панель получает данные и куда отправляет команды",
      value: this._settingsData.connection_mode === "center" ? "Совместимый API" : "Hausman Hub в Home Assistant",
      status: { className: "is-ready", label: "Активно" },
    }));
    const intercomConfigured = isIntercomQuickAccessVisible(this);
    grid.appendChild(this._settingsOverviewLink({
      viewId: "intercom", icon: "intercom", title: "Домофон",
      description: "Устройство открытия и быстрый доступ на экранах управления",
      value: intercomConfigured ? "Быстрый доступ включён" : "Не настроен",
      status: { className: intercomConfigured ? "is-ready" : "is-warning", label: intercomConfigured ? "Готов" : "Скрыт" },
    }));
    grid.appendChild(this._settingsOverviewLink({
      viewId: "appearance", icon: "sun", title: "Интерфейс и профиль",
      description: "Тема, размер, движение и подсказки для текущего пользователя",
      value: `Профиль Home Assistant · ${THEME_MODE_META[this._themeMode]?.hint || "авто"}`,
    }));
    grid.appendChild(this._settingsOverviewLink({
      viewId: "system", icon: "settings", title: "Диагностика",
      description: "Связь, сохранённая конфигурация и доступность компонентов",
      value: `Версия ${this._data.integration_version || "—"}`,
    }));
    container.appendChild(grid);
    const note = el("section", "card settings-help-card");
    note.appendChild(svgIcon("home"));
    const noteCopy = el("div");
    noteCopy.appendChild(el("strong", null, "Home Assistant остаётся единым источником устройств"));
    noteCopy.appendChild(el("p", "muted", "Hausman Hub использует комнаты, названия и сущности из Home Assistant. Изменения привязок явно сохраняются обратно в Home Assistant."));
    note.appendChild(noteCopy);
    container.appendChild(note);
  }

  _renderSettingsRooms(container) {
    renderSettingsRooms(this, container, {
      el,
      normalizedText,
      renderDeviceBindingCallout,
      renderDeviceInventory,
      roomSetupPanes: ROOM_SETUP_PANES,
      svgIcon,
    });
  }

  async _saveRoomType(room, roomType) {
    await saveRoomType(this, room, roomType, DASHBOARD_API);
  }

  _syncSettingsDirty() {
    this._settingsDirty = JSON.stringify(this._settingsData) !== JSON.stringify(this._settingsBaseline);
  }

  _updateSettingsActionControls() {
    const controls = this._settingsActionControls;
    if (!controls) return;
    controls.reset.disabled = this._busy || !this._settingsDirty;
    controls.save.disabled = this._busy || !this._settingsDirty;
    controls.state.textContent = this._settingsDirty
      ? "Есть несохранённые изменения"
      : "Все изменения сохранены";
  }

  _settingsStatusRow(title, value, tone = "") {
    const row = el("div", `settings-status-row${tone ? ` ${tone}` : ""}`);
    row.appendChild(el("span", null, title));
    row.appendChild(el("strong", null, value));
    return row;
  }

  _renderConnectionSettings(container) {
    const card = el("section", "card settings-card connection-settings-card");
    card.appendChild(el("h3", null, "Источник данных и команд"));
    card.appendChild(el(
      "p",
      "muted settings-card-intro",
      "Hausman Hub работает самостоятельно внутри Home Assistant: здесь хранятся настройки, выполняются сценарии и отправляются подтверждённые команды."
    ));
    const nativeMode = el("div", "settings-status-panel is-native-authority");
    nativeMode.appendChild(el("strong", null, "Единый центр управления"));
    nativeMode.appendChild(el("span", null, "Home Assistant + Hausman Hub HACS"));
    nativeMode.appendChild(el("small", null, "Внешний Center и Node-RED для работы не требуются."));
    card.appendChild(nativeMode);

    const form = el("div", "settings-form-grid connection-address-grid");
    const haLabel = el("label", "settings-field");
    haLabel.appendChild(el("span", "assistant-field-label", "Адрес Home Assistant"));
    const haInput = el("input");
    haInput.type = "url";
    haInput.value = this._settingsData.home_assistant_url;
    haInput.placeholder = "https" + "://homeassistant.local";
    haInput.addEventListener("input", () => {
      this._settingsData.home_assistant_url = haInput.value.trim();
      this._syncSettingsDirty();
      this._updateSettingsActionControls();
    });
    haLabel.appendChild(haInput);
    haLabel.appendChild(el("small", "settings-field-help", "Адрес для планшета и внешних клиентов. Текущая панель продолжит работать в этой вкладке."));
    form.appendChild(haLabel);
    card.appendChild(form);

    const status = el("div", "settings-status-panel");
    status.appendChild(el("strong", null, "Текущее состояние"));
    status.appendChild(this._settingsStatusRow("Панель Home Assistant", this._error ? "Недоступна" : "Доступна", this._error ? "is-warning" : "is-ready"));
    status.appendChild(this._settingsStatusRow(
      "Режим команд",
      "Hausman Hub в Home Assistant"
    ));
    card.appendChild(status);
    const check = el("button", "secondary settings-check", "Проверить доступность панели");
    check.type = "button";
    check.disabled = this._busy;
    check.addEventListener("click", () => this._checkConnection());
    card.appendChild(check);
    container.appendChild(card);

    const pageActions = el("div", "settings-page-actions");
    const actionState = el(
      "span",
      "settings-actions-state",
      this._settingsDirty ? "Есть несохранённые изменения" : "Все изменения сохранены"
    );
    pageActions.appendChild(actionState);
    const reset = el("button", "secondary", "Отменить изменения");
    reset.type = "button";
    reset.disabled = this._busy || !this._settingsDirty;
    reset.addEventListener("click", () => {
      this._settingsData = { ...this._settingsBaseline };
      this._settingsDirty = false;
      this._render();
    });
    const saveBtn = el("button", null, "Сохранить");
    saveBtn.disabled = this._busy || !this._settingsDirty;
    saveBtn.addEventListener("click", () => this._saveSettings());
    pageActions.appendChild(reset);
    pageActions.appendChild(saveBtn);
    this._settingsActionControls = { reset, save: saveBtn, state: actionState };
    container.appendChild(pageActions);
  }

  async _copySystemSummary() {
    const text = diagnosticSummaryText(this, buildDiagnosticChecks(this, READINESS_LABELS));
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else if (document.body && typeof document.execCommand === "function") {
        const field = document.createElement("textarea");
        field.value = text;
        document.body.appendChild(field);
        field.select();
        document.execCommand("copy");
        document.body.removeChild(field);
      } else {
        throw new Error("clipboard unavailable");
      }
      this._notice = "Техническая сводка скопирована без адресов и имён устройств.";
    } catch (error) {
      this._notice = "Не удалось скопировать сводку. Разрешите браузеру доступ к буферу обмена.";
    }
    this._render();
  }

  _renderSystemSettings(container) {
    const setup = this._settings.setup || {};
    const summary = setup.summary || {};
    const readiness = this._data && this._data.readiness || {};
    const checks = buildDiagnosticChecks(this, READINESS_LABELS);
    const errors = checks.filter((check) => check.tone === "is-error");
    const warnings = checks.filter((check) => check.tone === "is-warning");
    const overallTone = errors.length ? "is-error" : (warnings.length ? "is-warning" : "is-ready");
    const overallLabel = errors.length
      ? "Связь потеряна"
      : (warnings.length ? `Нужно проверить: ${warnings.length}` : "Все проверки пройдены");
    const health = el("section", "card settings-card system-health-card");
    const healthHead = el("div", "system-health-head");
    const healthCopy = el("div");
    healthCopy.appendChild(el("h3", null, "Состояние системы"));
    healthCopy.appendChild(el("p", "muted settings-card-intro", "Краткая проверка без названий сущностей, адресов и показаний дома."));
    healthHead.appendChild(healthCopy);
    healthHead.appendChild(el(
      "span",
      `status-badge ${overallTone}`,
      overallLabel
    ));
    health.appendChild(healthHead);
    const metrics = el("div", "system-health-metrics");
    [
      ["Версия", this._data && this._data.integration_version || "—"],
      ["Комнаты", Number(summary.room_count) || 0],
      ["Устройства климата", Number(summary.device_count) || 0],
      ["Режим", BRIDGE_MODE_LABELS[readiness.bridge_mode] || "Не настроен"],
    ].forEach(([label, value]) => {
      const metric = el("div", "system-health-metric");
      metric.appendChild(el("span", null, label));
      metric.appendChild(el("strong", null, value));
      metrics.appendChild(metric);
    });
    health.appendChild(metrics);
    renderDiagnosticDetails(this, health, checks, { el, svgIcon });
    const healthActions = el("div", "system-health-actions");
    const refresh = el("button", "secondary", "Обновить состояние");
    refresh.disabled = this._busy;
    refresh.addEventListener("click", () => this._refreshSystemState());
    const copy = el("button", "secondary", "Копировать техническую сводку");
    copy.addEventListener("click", () => this._copySystemSummary());
    healthActions.appendChild(refresh);
    healthActions.appendChild(copy);
    health.appendChild(healthActions);
    container.appendChild(health);

    renderTechnicalLogCard(this, container, { el });

    const about = el("section", "card settings-about");
    const aboutIcon = el("span", "settings-about-icon");
    aboutIcon.appendChild(brandMark());
    about.appendChild(aboutIcon);
    const aboutCopy = el("div", "settings-about-copy");
    aboutCopy.appendChild(el("h3", null, "Hausman Hub"));
    aboutCopy.appendChild(el("p", "muted", "Панель управления домом для Home Assistant"));
    aboutCopy.appendChild(el("small", null, "Единый интерфейс с планшетом Hausman Hub"));
    about.appendChild(aboutCopy);
    about.appendChild(el("span", "status-badge settings-version", `Версия ${this._data && this._data.integration_version || "—"}`));
    container.appendChild(about);

    const danger = el("section", "card settings-card danger-settings-card");
    danger.appendChild(el("h3", null, "Сброс Hausman Hub"));
    danger.appendChild(el("p", "muted settings-card-intro", "Удаляет настройки климата, локальные привязки, сценарии, ИК-коды, умного помощника и адреса подключения."));
    danger.appendChild(el("div", "reset-preserves", "Комнаты, области, сущности и устройства Home Assistant останутся без изменений."));
    const dangerActions = el("div", "danger-actions");
    if (this._resetArmed) {
      danger.appendChild(el("div", "reset-confirmation", "Действие нельзя отменить из панели. После сброса откроется мастер первичной настройки."));
      const cancelReset = el("button", "secondary", "Отмена");
      cancelReset.addEventListener("click", () => { this._resetArmed = false; this._render(); });
      const confirmReset = el("button", "danger-button", "Сбросить все настройки");
      confirmReset.disabled = this._busy;
      confirmReset.addEventListener("click", () => this._resetAllSettings());
      dangerActions.appendChild(cancelReset);
      dangerActions.appendChild(confirmReset);
    } else {
      const openReset = el("button", "secondary danger-outline", "Подготовить полный сброс");
      openReset.disabled = this._busy;
      openReset.addEventListener("click", () => { this._resetArmed = true; this._render(); });
      dangerActions.appendChild(openReset);
    }
    danger.appendChild(dangerActions);
    container.appendChild(danger);
  }

  _isEditingConnectionField() {
    const focused = this.shadowRoot && this.shadowRoot.activeElement;
    return (
      this._activeSection === "settings"
      && this._activeSettingsView === "connection"
      && this._settingsDirty
      && focused
      && focused.type === "url"
    );
  }

  _renderSettings(container) {
    container.innerHTML = "";
    const activeView = SETTINGS_VIEWS.find((view) => view.id === this._activeSettingsView) || SETTINGS_VIEWS[0];
    container.appendChild(createLibraryHero(this, {
      eyebrow: "ПАРАМЕТРЫ СИСТЕМЫ",
      title: `Настройки Hausman Hub · ${activeView.label}`,
      subtitle: activeView.description,
      statusLabel: this._tabletProfile ? "Профиль загружен" : "Профиль проверяется",
      facts: [
        { label: "РАЗДЕЛ", value: activeView.label },
        { label: "ПРОФИЛЬ", value: this._tabletProfile ? "Готов" : "Загрузка" },
        { label: "ИСТОЧНИК", value: "Home Assistant" },
      ],
    }, { el }));
    const nav = el("nav", "settings-subnav");
    setAttr(nav, "aria-label", "Разделы настроек");
    SETTINGS_VIEWS.forEach((view) => {
      const button = el("button", view.id === activeView.id ? "is-current" : "");
      button.type = "button";
      button.textContent = view.label;
      setAttr(button, "aria-current", view.id === activeView.id ? "page" : "false");
      button.addEventListener("click", () => this._activateSettingsView(view.id));
      nav.appendChild(button);
    });
    container.appendChild(nav);
    if (activeView.id === "overview") {
      this._renderSettingsOverview(container);
      return;
    }
    if (activeView.id === "rooms") {
      this._renderSettingsRooms(container);
      return;
    }
    if (activeView.id === "bindings") {
      renderDeviceBindings(this, container, { el, svgIcon });
      return;
    }
    if (activeView.id === "connection") this._renderConnectionSettings(container);
    if (activeView.id === "intercom") renderIntercomSettings(this, container, { el, setAttr, svgIcon });
    if (activeView.id === "appearance") renderAppearanceSettings(this, container, { el, selectField, setAttr, svgIcon });
    if (activeView.id === "system") this._renderSystemSettings(container);
  }

  async _resetAllSettings() {
    if (this._busy || !this._resetArmed) return;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      await this._hass.callApi("POST", RESET_API, { confirmation: "RESET_HAUSMANHUB" });
      this._themeMode = "auto";
      this._settingsPrefs = { large_text: false, reduced_motion: false, show_hints: true, rail_collapsed: false };
      this._persistUserPreferences();
      this._applyThemeMode();
      this._settingsData = { connection_mode: "home_assistant", smart_home_center_url: "", home_assistant_url: "" };
      this._settingsBaseline = { ...this._settingsData };
      this._settingsDirty = false;
      this._resetArmed = false;
      this._firstRun.completed = false;
      this._firstRun.deferred = false;
      this._firstRun.options = null;
      this._firstRun.rooms = {};
      this._firstRun.step = "instructions";
      clearFirstRunDraft(this);
      this._firstRunDraftReady = false;
      this._notice = "Настройки Hausman Hub сброшены. Перезапускается мастер.";
      if (window.location?.reload) setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      this._notice = apiErrorMessage(error);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _checkConnection() {
    if (this._busy || !this._hass) return;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      await this._hass.callApi("GET", PANEL_API);
      this._notice = "Панель доступна. Проверка не отправляла команды устройствам.";
      log(this, "success", "Проверка подключения выполнена успешно");
    } catch (error) {
      this._notice = apiErrorMessage(error);
      log(this, "error", "Проверка подключения завершилась ошибкой");
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _refreshSystemState() {
    if (this._busy || !this._hass) return;
    this._busy = true;
    this._notice = "";
    this._render();
    await this._load();
    this._busy = false;
    this._notice = this._error
      ? "Не удалось обновить состояние системы."
      : "Состояние системы обновлено.";
    this._render();
  }

  async _saveSettings() {
    if (this._busy || !this._settingsDirty) return;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      await this._hass.callApi("POST", CONNECTION_SETTINGS_API, this._settingsData);
      this._settingsBaseline = { ...this._settingsData };
      this._settingsDirty = false;
      this._notice = "Настройки подключения сохранены.";
      this._error = false;
      log(this, "success", "Настройки подключения сохранены");
    } catch (error) {
      this._notice = apiErrorMessage(error);
      log(this, "error", "Не удалось сохранить настройки подключения");
    } finally {
      this._busy = false;
    }
    await this._load();
  }
}

customElements.get?.("hausman-hub-panel") || customElements.define("hausman-hub-panel", HausmanHubPanel);
