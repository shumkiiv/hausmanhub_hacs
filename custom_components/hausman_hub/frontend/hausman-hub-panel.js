/* HausmanHub admin panel: climate overview, management, and full settings. */
const PANEL_API = "hausman_hub/v1/admin/panel";
const PANEL_CSS_URL = "/api/hausman_hub/panel/hausman-hub-panel.css";
const MODE_API = "hausman_hub/v1/admin/climate-mode";
const HOME_API = "hausman_hub/v1/admin/home-environment";
const WINDOWS_API = "hausman_hub/v1/admin/climate-room-signals";
const DRAFT_API = "hausman_hub/v1/admin/climate-drafts";
const SETUP_API = "hausman_hub/v1/admin/climate-drafts/current";
const DRAFT_VALIDATE_API = `${DRAFT_API}/validate`;
const DRAFT_SAVE_API = `${DRAFT_API}/save`;
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
const CONNECTION_SETTINGS_API = "hausman_hub/v1/admin/connection-settings";
const IR_CODES_API = "hausman_hub/v1/admin/ir-codes";
const IR_CODES_SCAN_API = `${IR_CODES_API}/scan`;
const IR_CODES_LEARN_API = `${IR_CODES_API}/learn`;
const IR_CODES_TEST_API = `${IR_CODES_API}/test`;
const IR_CODES_DELETE_API = `${IR_CODES_API}/delete`;
const IR_CODE_BINDINGS_API = `${IR_CODES_API}/bindings`;
const REFRESH_MS = 30000;

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
  direct_wifi: "Прямое управление (WiFi)",
};
const FIRST_RUN_STEPS = [
  "instructions", "rooms", "room", "home", "validation", "save", "code_source", "tablet", "completion", "success",
];
const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;
const ZIGBEE2MQTT_IMAGE_PATTERN =
  /^https:\/\/www\.zigbee2mqtt\.io\/images\/devices\/(?:[A-Za-z0-9._~-]|%[0-9A-F]{2})+\.png$/;
const PANEL_SECTIONS = [
  { id: "overview", label: "Обзор" },
  { id: "climate", label: "Климат" },
  { id: "scenarios", label: "Сценарии" },
  { id: "settings", label: "Настройки" },
];
const SECTION_SUBTITLES = {
  overview: "Состояние и управление домом",
  climate: "Климатический контур и комфорт",
  scenarios: "Управление сценариями дома",
  settings: "Подключение и параметры системы",
};
const READINESS_LABELS = {
  ready: "Система готова к управлению",
  not_ready: "Нужна настройка системы",
  unavailable: "Система временно недоступна",
  disabled: "Управление климатом выключено",
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

// Namespace URI is split so the module contains no absolute URL literals.
const SVG_NAMESPACE = "http" + "://www.w3.org/2000/svg";

const ICON_PATHS = {
  chevron: "M16.59 8.59 12 13.17 7.41 8.59 6 10l6 6 6-6z",
  device: "M4 6h18V4H4c-1.1 0-2 .9-2 2v11H0v3h14v-3H4zm19 2h-6c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h6c.55 0 1-.45 1-1V9c0-.55-.45-1-1-1m-1 9h-4v-7h4z",
  warning: "M1 21h22L12 2zm12-3h-2v-2h2zm0-4h-2v-4h2z",
  trash: "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6zm3.46-7.12 1.41-1.41L12 11.59l1.12-1.12 1.41 1.41L13.41 13l1.12 1.12-1.41 1.41L12 14.41l-1.12 1.12-1.41-1.41L10.59 13zM15.5 4l-1-1h-5l-1 1H5v2h14V4z",
  sun: "M6.76 4.84l-1.8-1.79-1.41 1.41 1.79 1.79 1.42-1.41zM4 10.5H1v2h3v-2zm9-9.95h-2V3.5h2V.55zm7.45 3.91l-1.41-1.41-1.79 1.79 1.41 1.41 1.79-1.79zm-3.21 13.7l1.79 1.8 1.41-1.41-1.8-1.79-1.4 1.4zM20 10.5v2h3v-2h-3zm-8-5c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm-1 16.95h2V19.5h-2v2.95zm-7.45-3.91l1.41 1.41 1.79-1.8-1.41-1.41-1.79 1.8z",
  moon: "M12 3c-4.97 0-9 4.03-9 9s4.03 9 9 9 9-4.03 9-9c0-.46-.04-.92-.1-1.36-.98 1.37-2.58 2.26-4.4 2.26-2.98 0-5.4-2.42-5.4-5.4 0-1.81.89-3.42 2.26-4.4-.44-.06-.9-.1-1.36-.1z",
  auto: "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18V4c4.41 0 8 3.59 8 8s-3.59 8-8 8z",
  home: "M12 3 2 12h3v9h6v-6h2v6h6v-9h3L12 3zm0 2.69L18 11v8h-3v-6H9v6H6v-8l6-5.31z",
  thermometer: "M15 13V5a3 3 0 0 0-6 0v8a5 5 0 1 0 6 0zm-3 6a3 3 0 0 1-1-5.83V5a1 1 0 0 1 2 0v8.17A3 3 0 0 1 12 19z",
  water: "M12 2.69 6.35 8.34A8 8 0 0 0 4 14a8 8 0 0 0 16 0c0-2.21-.9-4.21-2.35-5.66L12 2.69zM12 20a6 6 0 0 1-4.24-10.24L12 5.52l4.24 4.24A6 6 0 0 1 12 20z",
};

const THEME_MODES = ["auto", "light", "dark"];
const THEME_MODE_META = {
  auto: { icon: "auto", label: "Тема: авто (следует Home Assistant)", hint: "авто" },
  light: { icon: "sun", label: "Тема: светлая", hint: "светлая" },
  dark: { icon: "moon", label: "Тема: тёмная", hint: "тёмная" },
};

function svgIcon(name, className) {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  setAttr(svg, "viewBox", "0 0 24 24");
  setAttr(svg, "aria-hidden", "true");
  setAttr(svg, "focusable", "false");
  setAttr(svg, "class", className ? `icon ${className}` : "icon");
  const path = document.createElementNS(SVG_NAMESPACE, "path");
  setAttr(path, "d", ICON_PATHS[name]);
  setAttr(path, "fill", "currentColor");
  svg.appendChild(path);
  return svg;
}

function brandMark() {
  const mark = el("span", "brand-mark");
  setAttr(mark, "aria-hidden", "true");
  const shell = document.createElementNS(SVG_NAMESPACE, "svg");
  setAttr(shell, "viewBox", "0 0 31.7778 37.0833");
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
    this._settings = { mode: null, home: null, windows: null, setup: null };
    this._assistant = {
      data: null, error: false, fields: null, loaded: false, loading: false,
    };
    this._scenarios = { list: null, catalog: null, loading: false, error: false };
    this._settingsData = { connection_mode: "center", smart_home_center_url: "", home_assistant_url: "" };
    this._settingsDirty = false;
    this._error = false;
    this._busy = false;
    this._notice = "";
    this._themeMode = "auto";
    this._timer = null;
    this._shell = null;
    this._activeSection = null;
    this._expandedWizardRooms = new Set();
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
      schedule: { enabled: false, dayStart: "07:00", nightStart: "23:00" },
      setupRevision: null,
      step: "instructions",
      validRooms: new Set(),
      validation: null,
    };
    this._firstRunFields = null;
    this._onVisible = () => {
      if (!document.hidden) this._load();
    };
  }

  set hass(value) {
    const first = this._hass === null;
    this._hass = value;
    this._applyThemeMode();
    if (first) this._load();
  }

  connectedCallback() {
    this._timer = setInterval(() => this._load(), REFRESH_MS);
    document.addEventListener("visibilitychange", this._onVisible);
    this._render();
  }

  disconnectedCallback() {
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
    document.removeEventListener("visibilitychange", this._onVisible);
  }

  _applyThemeMode() {
    const darkMode = !!(this._hass && this._hass.themes && this._hass.themes.darkMode);
    const effective = this._themeMode === "auto" ? (darkMode ? "dark" : "light") : this._themeMode;
    if (this.classList && typeof this.classList.toggle === "function") {
      this.classList.toggle("theme-light", effective === "light");
    }
    this._updateThemeSwitcher();
  }

  _cycleThemeMode() {
    const index = THEME_MODES.indexOf(this._themeMode);
    this._themeMode = THEME_MODES[(index + 1) % THEME_MODES.length];
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
    if (!this._hass) return;
    try {
      const results = await Promise.all([
        this._hass.callApi("GET", PANEL_API),
        this._hass.callApi("GET", MODE_API).catch(() => null),
        this._hass.callApi("GET", HOME_API).catch(() => null),
        this._hass.callApi("GET", WINDOWS_API).catch(() => null),
        this._hass.callApi("GET", SETUP_API).catch(() => null),
        this._hass.callApi("GET", IR_CODE_BINDINGS_API).catch(() => ({ bindings: [] })),
      ]);
      this._data = results[0];
      this._settings = {
        mode: results[1],
        home: results[2],
        windows: results[3],
        setup: results[4],
        irBindings: results[5],
      };
      this._loadScenarios();
      this._loadSettings();
      this._error = false;
    } catch (error) {
      this._error = true;
    }
    this._render();
  }

  async _loadScenarios() {
    if (!this._hass) return;
    this._scenarios.loading = true;
    try {
      const [list, catalog] = await Promise.all([
        this._hass.callApi("GET", SCENARIOS_API).catch(() => null),
        this._hass.callApi("GET", SCENARIOS_CATALOG_API).catch(() => null),
      ]);
      this._scenarios.list = list;
      this._scenarios.catalog = catalog;
      this._scenarios.error = false;
    } catch (error) {
      this._scenarios.error = true;
    } finally {
      this._scenarios.loading = false;
      this._render();
    }
  }

  async _loadSettings() {
    if (!this._hass) return;
    try {
      const data = await this._hass.callApi("GET", CONNECTION_SETTINGS_API).catch(() => null);
      if (data && typeof data === "object") {
        this._settingsData = {
          connection_mode: data.connection_mode || "center",
          smart_home_center_url: data.smart_home_center_url || "",
          home_assistant_url: data.home_assistant_url || "",
        };
      }
    } catch (error) {
    }
    this._render();
  }

  async _post(path, payload, confirmText) {
    if (this._busy) return false;
    if (confirmText && !window.confirm(confirmText)) return false;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      const receipt = await this._hass.callApi("POST", path, payload);
      this._notice = this._receiptText(receipt);
      this._error = false;
      await this._load();
      return true;
    } catch (error) {
      this._notice = "Действие не выполнено. Проверьте состояние климата.";
      this._render();
      return false;
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _save(section, path, payload, confirmText, successText, conflictText) {
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
      this._notice = error && error.status === 409 ? conflictText
        : "Сохранить не удалось. Проверьте значения и состояние климата.";
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
    return (group && group[code]) || code;
  }

  _render() {
    if (!this.shadowRoot) return;
    this._ensureShell();
    const shell = this._shell;
    shell.notice.textContent = "";
    shell.notice.style.display = "none";
    if (this._error) {
      shell.banner.style.display = "";
      this._clearDynamic();
      return;
    }
    shell.banner.style.display = "none";
    if (!this._data) {
      shell.loading.style.display = "";
      this._clearDynamic();
      return;
    }
    shell.loading.style.display = "none";
    if (this._notice) {
      shell.notice.textContent = this._notice;
      shell.notice.style.display = "";
    }
    this._renderHeaderStatus(this._data.readiness);
    if (this._isFirstRunActive()) {
      this._activeSection = "overview";
      shell.brandSubtitle.textContent = "Первичная настройка дома";
      shell.nav.hidden = true;
      shell.wizard.hidden = false;
      PANEL_SECTIONS.forEach((section) => { shell.sectionNodes[section.id].hidden = true; });
      this._renderFirstRun(shell.wizard, this._settings.setup);
      return;
    }
    shell.wizard.hidden = true;
    shell.wizard.innerHTML = "";
    if (this._isFirstRunDeferred()) {
      shell.nav.hidden = true;
      this._activeSection = "overview";
      shell.brandSubtitle.textContent = SECTION_SUBTITLES.overview;
      this._renderHeaderStatus(this._data.readiness);
      this._renderReadiness(shell.readiness, this._data.readiness, this._data.snapshot);
      this._renderOverviewSummary(shell.summary, this._settings.setup, this._data.snapshot);
      this._renderRooms(shell.rooms, this._data.snapshot);
      PANEL_SECTIONS.forEach((section) => {
        shell.sectionNodes[section.id].hidden = section.id !== "overview";
      });
      return;
    }
    shell.nav.hidden = false;
    this._chooseInitialSection();
    this._renderReadiness(shell.readiness, this._data.readiness, this._data.snapshot);
    const snapshot = this._data.snapshot;
    this._renderOverviewSummary(shell.summary, this._settings.setup, snapshot);
    if (!this._dirty.wizard) {
      this._renderContour(shell.contour, snapshot, this._settings.setup);
    }
    this._renderRooms(shell.rooms, snapshot);
    if (!this._dirty.profiles) this._renderProfiles(shell.profiles, this._settings.setup);
    if (!this._dirty.schedule) this._renderSchedule(shell.schedule, this._settings);
    if (!this._dirty.home) this._renderHome(shell.home, this._settings.home);
    if (!this._dirty.windows) this._renderWindows(shell.windows, this._settings.windows);
    if (this._activeSection === "climate") this._renderAssistant(shell.assistant);
    if (this._activeSection === "scenarios") this._renderScenarios(shell.scenarios);
    if (this._activeSection === "settings") this._renderSettings(shell.settings);
    this._syncSectionVisibility();
  }

  _ensureShell() {
    if (this._shell) return;
    const root = this.shadowRoot;
    const stylesheet = el("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = PANEL_CSS_URL;
    root.appendChild(stylesheet);
    const container = el("main");
    root.appendChild(container);
    const header = el("header", "page-header");
    const brand = el("div", "page-brand");
    brand.appendChild(brandMark());
    const brandCopy = el("div", "brand-copy");
    brandCopy.appendChild(el("h1", "brand-title", "HausmanHub"));
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
    header.appendChild(headerActions);
    container.appendChild(header);
    const banner = el("div", "banner", "Данные HausmanHub недоступны. Проверьте интеграцию и повторите.");
    setAttr(banner, "role", "alert");
    banner.style.display = "none";
    container.appendChild(banner);
    const notice = el("div", "notice");
    setAttr(notice, "role", "status");
    setAttr(notice, "aria-live", "polite");
    notice.style.display = "none";
    container.appendChild(notice);
    const loading = el("div", "loading muted", "Загрузка данных HausmanHub…");
    loading.style.display = "none";
    container.appendChild(loading);
    const wizard = el("section", "first-run-wizard");
    setAttr(wizard, "aria-label", "Мастер первичной настройки HausmanHub");
    wizard.hidden = true;
    container.appendChild(wizard);
    const nav = el("nav", "tab-bar");
    setAttr(nav, "aria-label", "Разделы HausmanHub");
    setAttr(nav, "role", "tablist");
    const tabs = {};
    PANEL_SECTIONS.forEach((section, index) => {
      const button = el("button", "tab", section.label);
      button.type = "button";
      button.id = `hausman-tab-${section.id}`;
      setAttr(button, "data-section", section.id);
      setAttr(button, "role", "tab");
      setAttr(button, "aria-controls", `hausman-${section.id}`);
      button.addEventListener("click", () => this._activateSection(section.id));
      button.addEventListener("keydown", (event) => this._handleTabKey(event, index));
      nav.appendChild(button);
      tabs[section.id] = button;
    });
    container.appendChild(nav);
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
    const readiness = el("div");
    const summary = el("div");
    const rooms = el("div");
    sectionNodes.overview.appendChild(readiness);
    sectionNodes.overview.appendChild(summary);
    sectionNodes.overview.appendChild(rooms);
    const climate = sectionNodes.climate;
    const contour = el("div");
    const profiles = el("div");
    const schedule = el("div");
    const home = el("div");
    const windows = el("div");
    const assistant = el("div");
    climate.appendChild(contour);
    climate.appendChild(profiles);
    climate.appendChild(schedule);
    climate.appendChild(home);
    climate.appendChild(windows);
    climate.appendChild(assistant);
    const scenarios = el("div");
    sectionNodes.scenarios.appendChild(scenarios);
    const settings = el("div");
    sectionNodes.settings.appendChild(settings);
    this._shell = {
      banner, notice, loading, brandSubtitle, statusPill, versionBadge, themeButton, tabs, nav, sectionNodes, wizard,
      readiness, summary, rooms,
      contour, profiles, schedule, home, windows, assistant,
      scenarios, settings,
    };
    this._updateThemeSwitcher();
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
    this._activeSection = section;
    this._syncSectionVisibility();
    if (section === "climate") {
      this._renderAssistant(this._shell.assistant);
      if (!this._assistant.loaded) this._loadAssistant();
    }
    if (section === "scenarios") {
      this._renderScenarios(this._shell.scenarios);
      this._loadScenarios();
    }
    if (section === "settings") {
      this._loadSettings();
    }
    if (focus) focusNode(this._shell && this._shell.tabs[section]);
  }

  _handleTabKey(event, index) {
    const key = event && event.key;
    let next = null;
    if (key === "ArrowRight") next = (index + 1) % PANEL_SECTIONS.length;
    if (key === "ArrowLeft") next = (index - 1 + PANEL_SECTIONS.length) % PANEL_SECTIONS.length;
    if (key === "Home") next = 0;
    if (key === "End") next = PANEL_SECTIONS.length - 1;
    if (next === null) return;
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    this._activateSection(PANEL_SECTIONS[next].id, true);
  }

  _syncSectionVisibility() {
    if (!this._shell) return;
    this._shell.brandSubtitle.textContent = SECTION_SUBTITLES[this._activeSection]
      || "Настройка HausmanHub";
    const climateDirty = this._dirty.wizard || this._dirty.profiles || this._dirty.schedule || this._dirty.home || this._dirty.windows || this._dirty.assistant;
    const dirtyBySection = {
      climate: climateDirty,
      settings: this._settingsDirty,
    };
    PANEL_SECTIONS.forEach((section) => {
      const active = section.id === this._activeSection;
      this._shell.sectionNodes[section.id].hidden = !active;
      const tab = this._shell.tabs[section.id];
      setAttr(tab, "aria-current", active ? "page" : "false");
      setAttr(tab, "aria-selected", active ? "true" : "false");
      setAttr(tab, "tabindex", active ? "0" : "-1");
      const dirty = dirtyBySection[section.id];
      tab.className = `tab${dirty ? " is-dirty" : ""}`;
      tab.title = dirty ? "Есть несохранённые изменения" : "";
    });
  }

  _renderHeaderStatus(readiness) {
    const status = readiness && readiness.status;
    this._shell.statusPill.textContent = READINESS_LABELS[status] || "Состояние уточняется";
    setAttr(this._shell.statusPill, "data-status", status || "unknown");
    const version = this._data && this._data.integration_version;
    this._shell.versionBadge.textContent = version ? `Версия ${version}` : "";
    this._shell.versionBadge.style.display = version ? "" : "none";
  }

  _renderOverviewSummary(container, setup, snapshot) {
    container.innerHTML = "";
    if (!setup && !snapshot) return;
    const metrics = this._overviewMetrics(snapshot);
    const heading = el("div", "overview-section-heading");
    heading.appendChild(el("h2", null, "Сводка"));
    heading.appendChild(el("p", "section-intro", "Основные показатели дома"));
    container.appendChild(heading);
    const summary = el("div", "overview-summary");
    [
      ["thermometer", "Температура", metrics.temperature],
      ["water", "Влажность", metrics.humidity],
      ["device", "Активные устройства", `${metrics.activeDevices} из ${metrics.deviceCount}`],
    ].forEach(([iconName, label, value]) => {
      const item = el("div", "summary-item");
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

  _overviewMetrics(snapshot) {
    const rooms = snapshot && Array.isArray(snapshot.rooms) ? snapshot.rooms : [];
    const average = (values) => {
      const valid = values.filter((value) => typeof value === "number" && Number.isFinite(value));
      return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null;
    };
    const devices = rooms.flatMap((room) => Array.isArray(room.devices) ? room.devices : []);
    const activeDevices = devices.filter((device) => (
      !["off", "idle", "unavailable", "unknown"].includes(device.state)
    )).length;
    return {
      roomCount: rooms.length,
      deviceCount: devices.length,
      activeDevices,
      temperature: this._temp(average(rooms.map((room) => room.temperature))),
      humidity: this._humidity(average(rooms.map((room) => room.humidity))),
    };
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
      this._notice = "Сохранить настройки помощника не удалось. Проверьте значения.";
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
      this._notice = error && error.status === 422
        ? "Ошибка авторизации у поставщика. Проверьте ключ API."
        : "Обновить совет не удалось. Повторите позже.";
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
    heading.appendChild(el("h2", null, "Помощник AI"));
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
    presetLabel.appendChild(el("span", "assistant-field-label", "Поставщик AI"));
    const preset = selectField([
      { value: "deepseek", label: "DeepSeek" },
      { value: "openai", label: "OpenAI compatible" },
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
          const room = item.room_id ? ` (${item.room_id})` : "";
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
          const room = item.room_id ? ` (${item.room_id})` : "";
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

  _renderReadiness(container, readiness, snapshot) {
    container.innerHTML = "";
    const metrics = this._overviewMetrics(snapshot);
    const ready = readiness.status === "ready";
    const card = el("div", "card hero overview-hero");
    const head = el("div", "overview-hero-head");
    const icon = el("span", "overview-hero-icon");
    icon.appendChild(svgIcon("home"));
    head.appendChild(icon);
    const copy = el("div", "overview-hero-copy");
    copy.appendChild(el(
      "div",
      "hero-status",
      ready ? "Дом в комфортном режиме" : (READINESS_LABELS[readiness.status] || "Состояние уточняется")
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
    [
      [metrics.temperature, "Температура"],
      [metrics.humidity, "Влажность"],
      [metrics.activeDevices, "Устройств активно"],
      [metrics.roomCount, "Комнаты"],
    ].forEach(([value, label]) => {
      const metric = el("div", "overview-hero-metric");
      metric.appendChild(el("strong", null, value));
      metric.appendChild(el("span", "muted", label));
      metricGrid.appendChild(metric);
    });
    card.appendChild(metricGrid);
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
      const button = el(
        "button",
        managed ? "secondary" : null,
        managed ? "Выключить управление" : "Включить управление"
      );
      button.disabled = this._busy || (!managed && modeSettings.contour_configured !== true);
      button.addEventListener("click", () => {
        const target = managed ? "disabled" : "managed";
        this._save(
          "mode",
          MODE_API,
          { mode: target, expected_mode: modeSettings.mode, confirm: managed ? null : true },
          managed
            ? "Выключить управление климатом? Устройства больше не будут получать команды от HausmanHub."
            : "Включить управление климатом от HausmanHub? Убедитесь, что прежний модуль не управляет теми же устройствами.",
          managed ? "Управление климатом выключено." : "Управление климатом включено.",
          "Режим уже изменён в другом окне. Данные обновлены, повторите действие."
        );
      });
      switchRow.appendChild(button);
      card.appendChild(switchRow);
      if (!managed && modeSettings.contour_configured !== true) {
        card.appendChild(
          el("div", "muted", "Включение станет доступно после настройки климатического контура.")
        );
      }
    }
    container.appendChild(card);
  }

  _renderRooms(container, snapshot) {
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
      const card = el("div", "card overview-room-card");
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
      grid.appendChild(el("div", "card empty-state muted", "Комнаты пока не добавлены."));
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
    return (this._firstRun.options && this._firstRun.options.devices || []).filter((candidate) => (
      candidate.room_id === roomId
      || (candidate.can_add === true
        && candidate.room_id === "" && candidate.suggested_room_id === roomId)
    ));
  }

  _firstRunRoomlessCandidates() {
    return (this._firstRun.options && this._firstRun.options.devices || []).filter((candidate) => (
      candidate.can_add === true
      && candidate.room_id === ""
      && !candidate.suggested_room_id
    ));
  }

  _firstRunCandidateSelectable(candidate, room) {
    return candidate.can_add === true && (
      candidate.room_id === room.id
      || (candidate.room_id === ""
        && (candidate.suggested_room_id === room.id || !candidate.suggested_room_id))
    );
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
    return choices;
  }

  _firstRunDeviceGroups(choiceList, room, fields, allChoices, searchable) {
    const groups = el("div", "entity-groups");
    const grouped = new Map();
    choiceList.forEach((choice) => {
      const id = choice.candidate.device_group_id || `candidate:${choice.candidate.candidate_id}`;
      if (!grouped.has(id)) grouped.set(id, []);
      grouped.get(id).push(choice);
    });
    Array.from(grouped.entries()).forEach(([groupId, groupChoices]) => {
      const first = groupChoices[0].candidate;
      const group = el("div", "entity-group device-card");
      setAttr(group, "data-device-group-id", groupId);
      const header = el("div", "device-card-header");
      const thumb = el("div", "device-thumb");
      const fallback = el("span", "device-thumb-fallback");
      fallback.appendChild(svgIcon("device"));
      setAttr(fallback, "aria-hidden", "true");
      if (first.image_url && ZIGBEE2MQTT_IMAGE_PATTERN.test(first.image_url)) {
        const image = el("img");
        image.src = first.image_url;
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
      identity.appendChild(el("strong", "device-card-title", first.device_name || first.name));
      const details = [first.manufacturer, first.model].filter(Boolean);
      if (details.length) identity.appendChild(el("small", "device-card-meta", details.join(" · ")));
      const deviceTypeNames = ((this._firstRun.options || {}).display_names || {}).device_types || {};
      const chipTypes = [];
      groupChoices.forEach((choice) => {
        if (!choice.pseudo && !chipTypes.includes(choice.type)) chipTypes.push(choice.type);
      });
      if (chipTypes.length) {
        const chips = el("div", "device-card-chips");
        chipTypes.forEach((type) => chips.appendChild(el("span", "chip", deviceTypeNames[type] || type)));
        identity.appendChild(chips);
      }
      header.appendChild(identity);
      group.appendChild(header);
      const options = el("div", "device-card-options");
      groupChoices.sort((left, right) => left.order - right.order).forEach((choice) => {
        const selectable = !choice.pseudo && this._firstRunCandidateSelectable(choice.candidate, room);
        if (!selectable && choice.device.selected) {
          choice.device.selected = false;
          choice.device.channel = null;
        }
        const checkbox = el("input");
        checkbox.type = "checkbox";
        checkbox.checked = choice.device.selected;
        checkbox.value = choice.candidate.candidate_id;
        checkbox.disabled = !selectable || this._busy;
        const label = el("label", selectable ? "device-option" : "device-option is-disabled");
        label.appendChild(checkbox);
        const labelText = el("span", "entity-label");
        const deviceName = choice.pseudo
          ? "Тип не определён"
          : ((this._firstRun.options.display_names || {}).device_types || {})[choice.type] || choice.type;
        labelText.appendChild(el("strong", null, deviceName));
        labelText.appendChild(el("small", null, choice.candidate.name));
        const status = el("small", choice.candidate.status === "available" ? "status-badge is-ready" : "status-badge is-attention");
        status.textContent = choice.candidate.status === "unavailable"
          ? "Сейчас недоступно"
          : this._firstRunCandidateStatusName(choice.candidate);
        labelText.appendChild(status);
        const reason = el("small", "status-badge is-attention");
        reason.textContent = this._firstRunCandidateReasonName(choice.candidate);
        labelText.appendChild(reason);
        if (choice.candidate.room_id && choice.candidate.room_id !== room.id) {
          labelText.appendChild(el(
            "small",
            "status-badge is-attention",
            `Сейчас: ${this._firstRunCandidateRoomName(choice.candidate)}`
          ));
        }
        if (!selectable) labelText.appendChild(el("small", "muted", this._firstRunCandidateHint(choice.candidate, room)));
        const unavailableWarning = choice.candidate.status === "unavailable"
          ? el(
            "small",
            "device-unavailable-warning",
            `Устройство «${choice.candidate.name}» недоступно, оно будет применено, когда появится в сети.`
          )
          : null;
        if (unavailableWarning) {
          unavailableWarning.hidden = !choice.device.selected;
          labelText.appendChild(unavailableWarning);
        }
        label.appendChild(labelText);
        options.appendChild(label);
        let controlChannel = null;
        let channelRow = null;
        if (ACTIVE_DEVICE_TYPES.has(choice.type) && selectable) {
          controlChannel = selectField(
            [{ label: "Не выбран", value: "" }].concat((this._firstRun.options.control_channels || []).map((channel) => ({
              label: CONTROL_CHANNEL_LABELS[channel] || channel,
              value: channel,
            }))),
            choice.device.channel,
            () => {
              choice.device.channel = controlChannel.value || null;
              this._firstRunInvalidate(room.id);
            }
          );
          channelRow = el("label", "form-field", "Канал управления");
          channelRow.appendChild(controlChannel);
          channelRow.hidden = !choice.device.selected;
          options.appendChild(channelRow);
        }
        checkbox.addEventListener("change", () => {
          if (!selectable) return;
          choice.device.selected = checkbox.checked;
          if (checkbox.checked) {
            allChoices.forEach((peer) => {
              if (peer !== choice && peer.candidate.candidate_id === choice.candidate.candidate_id) {
                peer.device.selected = false;
                const peerField = fields.devices.find((item) => item.key === peer.key);
                if (peerField) {
                  peerField.checkbox.checked = false;
                  if (peerField.channelRow) peerField.channelRow.hidden = true;
                }
              }
            });
          }
          if (channelRow) channelRow.hidden = !checkbox.checked;
          if (unavailableWarning) unavailableWarning.hidden = !checkbox.checked;
          this._firstRunInvalidate(room.id);
        });
        fields.devices.push({ checkbox, controlChannel, channelRow, key: choice.key, type: choice.type });
      });
      group.appendChild(options);
      groups.appendChild(group);
      searchable.push({
        group,
        text: normalizedText([first.name, first.device_name, first.manufacturer, first.model].filter(Boolean).join(" ")),
      });
    });
    return groups;
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
        { commandName: "humidifier.on", label: "on" },
        { commandName: "humidifier.off", label: "off" },
      ];
    }
    const temperatures = [device.profiles.day, device.profiles.night]
      .map((profile) => Number(profile && profile.target_temperature))
      .filter((temperature) => Number.isFinite(temperature));
    const commands = [{ commandName: "ac.off", label: "off" }];
    Array.from(new Set(temperatures)).forEach((temperature) => {
      const label = `cool ${temperature.toFixed(1)} °C`;
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
      ir.error = "Не удалось загрузить источники или импортированные ИК-коды.";
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
      ir.error = "Импортировать ИК-код не удалось. Проверьте источник и повторите.";
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
      ir.error = "Тестовую отправку ИК-кода выполнить не удалось.";
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
      ir.error = "Тестовую отправку ИК-кода выполнить не удалось.";
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
      ir.error = "Удалить ИК-код не удалось. Повторите попытку.";
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
        ir.error = "Обучение ИК-кода не удалось. Проверьте пульт и повторите.";
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
    this._firstRun.loading = true;
    this._firstRun.optionsError = false;
    this._render();
    try {
      this._firstRun.options = await this._hass.callApi("GET", DRAFT_API);
      Object.keys(this._firstRun.rooms).forEach((roomId) => {
        const room = (this._firstRun.options.rooms || []).find((item) => item.id === roomId);
        if (room) this._firstRunRoomState(room);
      });
      if (force) {
        Object.values(this._firstRun.rooms).forEach((state) => { state.report = null; });
        this._firstRun.draft = null;
        this._firstRun.issues = [];
        this._firstRun.validation = null;
        this._firstRun.validRooms.clear();
      }
    } catch (error) {
      this._firstRun.optionsError = true;
    } finally {
      this._firstRun.loading = false;
    }
    this._render();
  }

  _deferFirstRun() {
    this._firstRun.deferred = true;
    this._activeSection = "overview";
    this._render();
  }

  _openFirstRunRoom(roomId) {
    this._firstRun.roomId = roomId;
    this._firstRun.step = "room";
    this._render();
  }

  _firstRunBackToRooms() {
    this._firstRun.roomId = null;
    this._firstRun.step = "rooms";
    this._render();
  }

  async _checkFirstRunRoom(roomId) {
    const collected = this._firstRunPayload([roomId]);
    if (collected.error) {
      const state = this._firstRun.rooms[roomId];
      state.report = { issues: [{ message: collected.error }], save_allowed: false, status: "blocked" };
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
        issues: [{ message: "Проверить комнату не удалось. Проверьте выбранные значения." }],
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
    if (
      home.heating_lockout_high === "" || home.heating_lockout_low === ""
      || !Number.isFinite(high) || !Number.isFinite(low) || high < -40 || high > 60
      || low < -40 || low > 60 || low >= high
    ) {
      this._firstRun.homeError = "Проверьте пороги: от -40 до 60 °C, нижний строго меньше верхнего.";
      this._render();
      return;
    }
    this._busy = true;
    this._firstRun.homeError = "";
    this._render();
    try {
      await this._hass.callApi("POST", HOME_API, {
        central_heating_entity_id: home.central_heating_entity_id || null,
        heating_lockout_high: high,
        heating_lockout_low: low,
        outdoor_temperature_entity_id: home.outdoor_temperature_entity_id || null,
        presence_entity_id: home.presence_entity_id || null,
      });
      this._firstRun.step = "validation";
    } catch (error) {
      this._firstRun.homeError = "Сигналы дома сохранить не удалось. Проверьте значения и повторите.";
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
      this._firstRun.issues = [{ message: "Полная проверка не выполнена. Повторите попытку." }];
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
      if (contourSaved) {
        this._firstRun.completed = true;
        this._activeSection = "overview";
        this._notice = "Контур сохранён, но профили или расписание не сохранились. Откройте соответствующие вкладки и повторите сохранение.";
      } else if (error && error.status === 409) {
        this._firstRun.conflict = true;
      } else {
        this._firstRun.issues = [{ message: "Сохранить настройку не удалось. Проверьте состояние и повторите." }];
      }
      this._firstRun.step = "completion";
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _reloadFirstRun() {
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
      instructions: "Инструкция",
      rooms: "Комнаты",
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
    this._renderFirstRunProgress(container);
    const card = el("article", "card");
    container.appendChild(card);
    if (this._firstRun.step === "instructions") {
      card.appendChild(el("h2", null, "Первичная настройка климата"));
      card.appendChild(el("p", "section-intro", "Сначала назначьте устройства и датчики областям Home Assistant. Мастер использует области как состав комнат и не создаёт отдельные комнаты."));
      const list = el("ol", "reasons");
      [
        "Откройте Настройки Home Assistant → Устройства и службы → Устройства и назначьте каждому устройству область.",
        "Проверьте сами области в Настройки → Области.",
        "Вернитесь сюда: мастер покажет устройства только в области, к которой они привязаны.",
      ].forEach((text) => list.appendChild(el("li", null, text)));
      card.appendChild(list);
      card.appendChild(el("div", "candidate-room-warning", "Если область у устройства не задана, мастер не может безопасно предложить его для комнаты."));
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
    card.appendChild(el("h2", null, "Выберите комнаты"));
    card.appendChild(el("div", "section-intro", "Проверьте каждую нужную область отдельно. В настройку попадут только комнаты, которые успешно прошли проверку."));
    const roomlessCandidates = this._firstRunRoomlessCandidates();
    if (roomlessCandidates.length) {
      card.appendChild(el(
        "div",
        "wizard-warning",
        `Устройства без комнаты: ${roomlessCandidates.length}. Они доступны на шаге комнаты в секции «Устройства без комнаты» или после назначения зоны в Home Assistant.`
      ));
    }
    const fields = { rooms: {} };
    const list = el("div", "first-run-room-list");
    (this._firstRun.options.rooms || []).forEach((room) => {
      const state = this._firstRunRoomState(room);
      const candidates = this._firstRunRoomCandidates(room.id);
      const active = candidates.filter((candidate) => (
        (candidate.suggested_types || []).some((type) => ACTIVE_DEVICE_TYPES.has(type))
      )).length;
      const sensors = candidates.filter((candidate) => (
        (candidate.suggested_types || []).some((type) => SENSOR_DEVICE_TYPES.has(type))
      )).length;
      const roomCard = el("article", "card first-run-room");
      roomCard.appendChild(el("h3", null, room.name || room.id));
      const include = el("input");
      include.type = "checkbox";
      include.checked = state.included;
      include.disabled = room.selectable !== true || this._busy;
      const includeLabel = el("label", "checkbox-field");
      includeLabel.appendChild(include);
      includeLabel.appendChild(el("span", null, "Включить комнату в климатический контур"));
      roomCard.appendChild(includeLabel);
      roomCard.appendChild(el(
        "div",
        "muted",
        `Найдено: устройств управления - ${active}, датчиков - ${sensors}.`
      ));
      const badge = el("span", "status-badge");
      if (this._firstRun.validRooms.has(room.id)) {
        badge.textContent = "Настроена";
        badge.className += " is-ready";
      } else if (state.report && (state.report.issues || []).length) {
        badge.textContent = "Требует внимания";
        badge.className += " is-attention";
      } else {
        badge.textContent = "Не настроена";
      }
      roomCard.appendChild(badge);
      const actions = el("div", "actions");
      const configure = el("button", "secondary", "Настроить");
      configure.disabled = include.disabled;
      configure.addEventListener("click", () => this._openFirstRunRoom(room.id));
      actions.appendChild(configure);
      roomCard.appendChild(actions);
      include.addEventListener("change", () => {
        state.included = include.checked;
        this._firstRunInvalidate(room.id);
        this._render();
      });
      fields.rooms[room.id] = { configure, include };
      list.appendChild(roomCard);
    });
    if (!(this._firstRun.options.rooms || []).length) {
      list.appendChild(el("div", "card empty-state muted", "Home Assistant пока не передал ни одной области."));
    }
    card.appendChild(list);
    const actions = el("div", "actions");
    const back = el("button", "secondary", "Назад к инструкции");
    back.disabled = this._busy;
    back.addEventListener("click", () => {
      this._firstRun.step = "instructions";
      this._render();
    });
    const finish = el("button", null, "Завершить настройку");
    finish.disabled = this._busy || this._firstRun.validRooms.size === 0;
    finish.title = finish.disabled
      ? "Сначала успешно проверьте хотя бы одну комнату."
      : "Перейти к параметрам дома и общей проверке.";
    finish.addEventListener("click", () => {
      this._firstRun.step = "home";
      this._render();
    });
    actions.appendChild(back);
    actions.appendChild(finish);
    card.appendChild(actions);
    if (finish.disabled) {
      card.appendChild(el("div", "muted action-help", "Кнопка станет доступна после успешной проверки хотя бы одной комнаты."));
    }
    this._firstRunFields = fields;
  }

  _renderFirstRunRoom(card) {
    const room = (this._firstRun.options.rooms || []).find((item) => item.id === this._firstRun.roomId);
    if (!room) {
      this._firstRunBackToRooms();
      return;
    }
    const state = this._firstRunRoomState(room);
    card.appendChild(el("h2", null, `Комната: ${room.name || room.id}`));
    card.appendChild(el("div", "section-intro", "Изменения остаются в черновике мастера, пока комната не пройдёт проверку."));
    const fields = { devices: [], maxTemperature: null, minTemperature: null, room: null };
    const scheduleSection = el("section", "wizard-section");
    scheduleSection.appendChild(el("h3", null, "Расписание комнаты"));
    const dayStart = el("input");
    dayStart.type = "time";
    dayStart.value = this._firstRun.schedule.dayStart;
    const dayRow = el("label", "form-field", "Начало дня");
    dayRow.appendChild(dayStart);
    scheduleSection.appendChild(dayRow);
    const nightStart = el("input");
    nightStart.type = "time";
    nightStart.value = this._firstRun.schedule.nightStart;
    const nightRow = el("label", "form-field", "Начало ночи");
    nightRow.appendChild(nightStart);
    scheduleSection.appendChild(nightRow);
    const automatic = el("input");
    automatic.type = "checkbox";
    automatic.checked = this._firstRun.schedule.enabled === true;
    const managed = this._settings.mode && this._settings.mode.mode === "managed";
    automatic.disabled = !managed || this._busy;
    const automaticLabel = el("label", "checkbox-field");
    automaticLabel.appendChild(automatic);
    automaticLabel.appendChild(el("span", null, "Автоматически переключать дневной и ночной профиль"));
    scheduleSection.appendChild(automaticLabel);
    if (!managed) {
      scheduleSection.appendChild(el("div", "muted", "Автопереключение можно включить после сохранения контура и включения управляемого режима."));
    }
    [dayStart, nightStart, automatic].forEach((control) => {
      control.addEventListener("input", () => {
        this._firstRun.schedule.dayStart = dayStart.value;
        this._firstRun.schedule.nightStart = nightStart.value;
        this._firstRun.schedule.enabled = automatic.checked === true;
        this._firstRunInvalidate();
      });
      control.addEventListener("change", () => {
        this._firstRun.schedule.dayStart = dayStart.value;
        this._firstRun.schedule.nightStart = nightStart.value;
        this._firstRun.schedule.enabled = automatic.checked === true;
        this._firstRunInvalidate();
      });
    });
    card.appendChild(scheduleSection);

    const profilesSection = el("section", "wizard-section");
    profilesSection.appendChild(el("h3", null, "Дневной и ночной профиль"));
    profilesSection.appendChild(el("div", "muted", "Цели сохраняются раздельно для дня и ночи. Температура 18-28 °C с шагом 0,5, влажность 30-70 % с шагом 1."));
    const columns = el("div", "profile-columns");
    ["day", "night"].forEach((profile) => {
      const values = state[profile];
      const block = el("div", "profile-block");
      block.appendChild(el("h4", null, profile === "day" ? "День" : "Ночь"));
      const temperature = numberField(values.temperature, 18, 28, 0.5, () => {
        values.temperature = temperature.value;
        this._firstRunInvalidate(room.id);
      });
      const temperatureRow = el("label", "form-field", "Температура, °C");
      temperatureRow.appendChild(temperature);
      block.appendChild(temperatureRow);
      const humidity = numberField(values.humidity, 30, 70, 1, () => {
        values.humidity = humidity.value;
        this._firstRunInvalidate(room.id);
      });
      const humidityRow = el("label", "form-field", "Влажность, %");
      humidityRow.appendChild(humidity);
      block.appendChild(humidityRow);
      const strategy = selectField(
        STRATEGY_ORDER.map((code) => ({
          label: ((this._firstRun.options.display_names || {}).strategies || {})[code] || code,
          value: code,
        })),
        values.strategy,
        () => {
          values.strategy = strategy.value;
          this._firstRunInvalidate(room.id);
        }
      );
      const strategyRow = el("label", "form-field", "Стратегия");
      strategyRow.appendChild(strategy);
      block.appendChild(strategyRow);
      columns.appendChild(block);
    });
    profilesSection.appendChild(columns);
    card.appendChild(profilesSection);

    const limitsSection = el("section", "wizard-section");
    limitsSection.appendChild(el("h3", null, "Допустимая температура комнаты"));
    limitsSection.appendChild(el("div", "muted", "Необязательные границы ограничивают команды кондиционеру и отоплению. Пустое поле означает, что дополнительной границы нет."));
    const minTemperature = numberField(
      state.minTemperature === null ? "" : state.minTemperature,
      18, 28, 0.5,
      () => {
        state.minTemperature = minTemperature.value;
        this._firstRunInvalidate(room.id);
      }
    );
    const minRow = el("label", "form-field", "Минимальная температура, °C");
    minRow.appendChild(minTemperature);
    limitsSection.appendChild(minRow);
    const maxTemperature = numberField(
      state.maxTemperature === null ? "" : state.maxTemperature,
      18, 28, 0.5,
      () => {
        state.maxTemperature = maxTemperature.value;
        this._firstRunInvalidate(room.id);
      }
    );
    const maxRow = el("label", "form-field", "Максимальная температура, °C");
    maxRow.appendChild(maxTemperature);
    limitsSection.appendChild(maxRow);
    card.appendChild(limitsSection);

    const devicesSection = el("section", "wizard-section");
    devicesSection.appendChild(el("h3", null, "Устройства и датчики"));
    devicesSection.appendChild(el("div", "muted", "Канал управления сохраняется в контуре и честно показывает транспорт устройства. Если устройство - климатическая обёртка (например, SmartIR), команды выполняются сразу через стандартные сервисы Home Assistant при любом канале. Без такой обёртки сырой ИК-пульт остаётся только в наблюдении."));
    const roomlessCandidates = this._firstRunRoomlessCandidates();
    if (roomlessCandidates.length) {
      const visibleNames = roomlessCandidates.slice(0, 5).map((candidate) => (
        candidate.name || candidate.device_name || candidate.candidate_id
      ));
      const remaining = roomlessCandidates.length - visibleNames.length;
      const names = `${visibleNames.join(", ")}${remaining ? ` и ещё ${remaining}` : ""}`;
      devicesSection.appendChild(el(
        "div",
        "wizard-warning",
        `Внимание: найдены устройства без комнаты: ${names}. Они не показаны в списке комнаты. Отметьте нужные в секции «Устройства без комнаты» ниже - привязка сохранится только в HausmanHub, зоны Home Assistant не изменятся. Либо назначьте устройству зону в Home Assistant и нажмите «Обновить список устройств».`
      ));
    }
    const deviceActions = el("div", "actions");
    const refreshDevices = el("button", "secondary", "Обновить список устройств");
    refreshDevices.disabled = this._busy || this._firstRun.loading;
    refreshDevices.addEventListener("click", () => this._loadFirstRunOptions(true));
    deviceActions.appendChild(refreshDevices);
    devicesSection.appendChild(deviceActions);
    const showAll = el("input");
    showAll.type = "checkbox";
    showAll.checked = state.showAllDevices === true;
    showAll.disabled = this._busy;
    const showAllLabel = el("label", "checkbox-field");
    showAllLabel.appendChild(showAll);
    showAllLabel.appendChild(el("span", null, "Показать все устройства"));
    devicesSection.appendChild(showAllLabel);
    const roomChoices = this._firstRunRoomChoices(state, this._firstRunRoomCandidates(room.id));
    const roomlessChoices = this._firstRunRoomChoices(state, roomlessCandidates);
    const nearbyChoices = this._firstRunRoomChoices(state, this._firstRunPossibleRoomCandidates(room));
    const catalogChoices = this._firstRunRoomChoices(
      state,
      (this._firstRun.options && this._firstRun.options.devices) || []
    );
    const choices = roomChoices.concat(roomlessChoices, nearbyChoices);
    const irRemotes = (this._firstRun.options.ir_remotes || []).filter((remote) => remote.room_id === room.id);
    const hasClimateFacade = choices.some((choice) => (
      choice.type === "air_conditioner" || choice.type === "humidifier"
    ));
    if (irRemotes.length && !hasClimateFacade) {
      const names = irRemotes.map((remote) => `«${remote.name}»`).join(", ");
      devicesSection.appendChild(el("div", "wizard-hint", `В комнате найден ИК-пульт ${names}, но климатической обёртки для него нет. Чтобы управлять кондиционером или увлажнителем через такой пульт, создайте в Home Assistant climate-обёртку SmartIR с готовым кодом устройства, привяжите её к этой зоне и обновите список: обёртка появится кандидатом, и управление заработает сразу.`));
    }
    const search = el("input", "entity-search");
    search.type = "search";
    search.placeholder = "Найти устройство или датчик";
    setAttr(search, "aria-label", "Поиск устройств комнаты");
    devicesSection.appendChild(search);
    const searchable = [];
    if (state.showAllDevices) {
      const byRoom = new Map();
      catalogChoices.forEach((choice) => {
        const roomId = choice.candidate.room_id || "";
        if (!byRoom.has(roomId)) byRoom.set(roomId, []);
        byRoom.get(roomId).push(choice);
      });
      Array.from(byRoom.entries())
        .sort(([left], [right]) => {
          if (!left) return 1;
          if (!right) return -1;
          return this._firstRunCandidateRoomName({ room_id: left }).localeCompare(
            this._firstRunCandidateRoomName({ room_id: right }), "ru"
          );
        })
        .forEach(([roomId, groupedChoices]) => {
          devicesSection.appendChild(el("h4", null, roomId
            ? this._firstRunCandidateRoomName({ room_id: roomId }) : "Без комнаты"));
          devicesSection.appendChild(this._firstRunDeviceGroups(
            groupedChoices, room, fields, catalogChoices, searchable
          ));
        });
      if (!catalogChoices.length) {
        devicesSection.appendChild(el("div", "muted", "Home Assistant пока не передал ни одного устройства."));
      }
    } else {
      const groups = this._firstRunDeviceGroups(roomChoices, room, fields, choices, searchable);
      if (!roomChoices.length) groups.appendChild(el("div", "muted", "Подходящих устройств в этой области пока нет."));
      devicesSection.appendChild(groups);
      if (roomlessChoices.length) {
        devicesSection.appendChild(this._collapsibleDeviceSection(
          "Устройства без комнаты",
          "Эти устройства не привязаны ни к одной зоне Home Assistant. Отметьте нужные, чтобы привязать их к этой комнате только в HausmanHub: зоны Home Assistant не изменятся.",
          this._firstRunDeviceGroups(roomlessChoices, room, fields, choices, searchable),
          false
        ));
      }
      if (nearbyChoices.length) {
        devicesSection.appendChild(this._collapsibleDeviceSection(
          "Возможно, относится к этой комнате",
          "Эти климатические устройства уже привязаны к другой области Home Assistant и показаны только для проверки.",
          this._firstRunDeviceGroups(nearbyChoices, room, fields, choices, searchable),
          true
        ));
      }
    }
    showAll.addEventListener("change", () => {
      state.showAllDevices = showAll.checked === true;
      this._render();
    });
    search.addEventListener("input", () => {
      const query = normalizedText(search.value);
      searchable.forEach((entry) => { entry.group.hidden = Boolean(query) && !entry.text.includes(query); });
    });
    card.appendChild(devicesSection);

    const report = state.report;
    if (report) {
      const reportBox = el("div", "wizard-report");
      const ready = report.status === "ready" && report.save_allowed === true;
      reportBox.appendChild(el("strong", null, ready ? "Комната проверена" : "Проверка требует внимания"));
      const issues = report.issues || [];
      if (issues.length) {
        const list = el("ul");
        issues.forEach((issue) => list.appendChild(el(
          "li",
          issue.level === "warning" ? "issue-warning" : null,
          issue.message || "Проверьте настройки комнаты."
        )));
        reportBox.appendChild(list);
      } else if (ready) {
        reportBox.appendChild(el("div", "muted", "Все выбранные устройства и цели прошли проверку."));
      }
      card.appendChild(reportBox);
    }
    const actions = el("div", "actions");
    const back = el("button", "secondary", "Назад к списку комнат");
    back.disabled = this._busy;
    back.addEventListener("click", () => this._firstRunBackToRooms());
    const check = el("button", null, "Проверить комнату");
    check.disabled = this._busy || room.selectable !== true;
    check.title = check.disabled ? "Комната недоступна для настройки." : "Проверить цели и выбранные привязки.";
    check.addEventListener("click", () => this._checkFirstRunRoom(room.id));
    actions.appendChild(back);
    actions.appendChild(check);
    card.appendChild(actions);
    fields.maxTemperature = maxTemperature;
    fields.minTemperature = minTemperature;
    fields.room = room;
    this._firstRunFields = { room: fields };
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
        outdoor_temperature_entity_id: saved.outdoor_temperature_entity_id || null,
        presence_entity_id: saved.presence_entity_id || null,
      };
    }
    const home = this._firstRun.home;
    const candidates = (this._settings.home && this._settings.home.candidates) || {};
    const pickers = {};
    [
      ["outdoor_temperature_entity_id", "Наружная температура", "sensor с температурой или погодный сервис Home Assistant.", "outdoor_temperature"],
      ["presence_entity_id", "Общее присутствие дома", "Этот сигнал задаёт политику «дома/нет дома» для всего дома.", "presence"],
      ["central_heating_entity_id", "Центральное отопление", "Сигнал показывает работу центрального отопления.", "central_heating"],
    ].forEach(([key, title, helper, kind]) => {
      const picker = this._singleChoicePicker({
        candidates: candidates[kind] || [],
        current: home[key],
        helper,
        onChange: () => {
          home[key] = picker.value() || null;
        },
        signalKind: kind,
        title,
      });
      card.appendChild(picker.root);
      pickers[key] = picker;
    });
    const high = numberField(home.heating_lockout_high, -40, 60, 0.5, () => {
      home.heating_lockout_high = high.value;
    });
    const highRow = el("label", "form-field", "Не греть выше, °C");
    highRow.appendChild(high);
    card.appendChild(highRow);
    card.appendChild(el("div", "muted field-help", "Выше этого порога на улице отопление не включается: дома уже достаточно тепло."));
    const low = numberField(home.heating_lockout_low, -40, 60, 0.5, () => {
      home.heating_lockout_low = low.value;
    });
    const lowRow = el("label", "form-field", "Аварийная защита ниже, °C");
    lowRow.appendChild(low);
    card.appendChild(lowRow);
    card.appendChild(el("div", "muted field-help", "Ниже этого порога защита снова разрешает отопление даже после тёплого периода."));
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
      Object.keys(pickers).forEach((key) => { home[key] = pickers[key].value() || null; });
      this._saveFirstRunHome();
    });
    actions.appendChild(back);
    actions.appendChild(next);
    card.appendChild(actions);
  }

  _renderFirstRunValidation(card) {
    card.appendChild(el("h2", null, "Проверка настройки"));
    card.appendChild(el("div", "section-intro", "Перед сохранением HausmanHub проверит все выбранные комнаты, устройства и границы одним черновиком."));
    const validation = this._firstRun.validation;
    if (validation) {
      const ready = validation.status === "ready" && validation.save_allowed === true;
      const report = el("div", "wizard-report");
      report.appendChild(el("strong", null, ready ? "Настройка прошла проверку" : "Найдены замечания"));
      const issues = this._firstRun.issues || [];
      if (issues.length) {
        const list = el("ul");
        issues.forEach((issue) => {
          const line = el("li");
          line.appendChild(el(
            "span",
            issue.level === "warning" ? "issue-warning" : null,
            issue.message || "Проверьте настройку."
          ));
          if (issue.room_id) {
            const fix = el("button", "secondary", "Исправить комнату");
            fix.addEventListener("click", () => this._openFirstRunRoom(issue.room_id));
            line.appendChild(fix);
          }
          list.appendChild(line);
        });
        report.appendChild(list);
      } else if (ready) {
        report.appendChild(el("div", "muted", "Все выбранные комнаты готовы к сохранению."));
      }
      card.appendChild(report);
    }
    const actions = el("div", "actions");
    const back = el("button", "secondary", "Назад к параметрам дома");
    back.disabled = this._busy;
    back.addEventListener("click", () => {
      this._firstRun.step = "home";
      this._render();
    });
    const check = el("button", null, "Проверить настройку");
    check.disabled = this._busy;
    check.addEventListener("click", () => this._validateFirstRun());
    actions.appendChild(back);
    actions.appendChild(check);
    if (validation && validation.status === "ready" && validation.save_allowed === true) {
      const next = el("button", null, "Перейти к сохранению");
      next.disabled = this._busy;
      next.addEventListener("click", () => {
        this._firstRun.step = "save";
        this._render();
      });
      actions.appendChild(next);
    }
    card.appendChild(actions);
  }

  _renderFirstRunCodeSource(card) {
    const ir = this._firstRun.ir;
    const devices = this._firstRunIrDevices();
    const device = this._firstRunActiveIrDevice();
    card.appendChild(el("h2", null, "Источник IR-кодов"));
    card.appendChild(el("div", "section-intro", "Добавьте коды для ИК-пульта из базы, сохранённых команд Broadlink или через обучение. Коды привязаны к уже сохранённому устройству контура."));
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
    card.appendChild(el("div", "section-intro", "В приложении планшета укажите адрес этого Home Assistant и личный токен пользователя с нужными правами."));
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
      "GET /endpoint/smart-home-center/api для сводки дома.",
      "POST /endpoint/smart-home-center/action для подтверждённых действий.",
      "GET и POST /endpoint/climate/api/v1/state и command для климата.",
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
    card.appendChild(el("div", "wizard-success", "Контур создан. Сейчас откроется обычная панель HausmanHub."));
  }

  _renderContourWizard(container, setup) {
    if (!setup) {
      container.appendChild(el("div", "card muted", "Настройка контура временно недоступна."));
      return;
    }
    const configured = setup.status !== "not_configured";
    if (configured && !this._wizard.open) {
      const card = el("div", "card");
      card.appendChild(el("h3", null, setup.name || "Климатический контур"));
      const modes = (setup.display_names && setup.display_names.modes) || {};
      this._row(card, "Режим", modes[setup.mode] || setup.mode);
      const summary = setup.summary || {};
      this._row(card, "Комнат", summary.room_count || 0);
      this._row(card, "Устройств", summary.device_count || 0);
      (setup.issues || []).forEach((issue) => {
        if (issue && issue.message) card.appendChild(el("div", "wizard-issues", issue.message));
      });
      const edit = el("button", null, "Изменить контур");
      edit.disabled = this._busy || setup.editing_allowed !== true;
      edit.addEventListener("click", () => this._openWizard(setup));
      card.appendChild(edit);
      if (this._firstRunIrDevices().length) {
        const irCodes = el("button", "secondary", "Настроить IR-коды");
        irCodes.disabled = this._busy;
        irCodes.addEventListener("click", () => this._openSavedIrCodeSetup());
        card.appendChild(irCodes);
      }
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
      if (error && error.status === 409) {
        await this._resetWizardAfterConflict();
      } else {
        this._showWizardMessage("Проверить контур не удалось. Проверьте значения и состояние устройств.");
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
      if (error && error.status === 409) {
        await this._resetWizardAfterConflict();
      } else {
        this._showWizardMessage("Сохранить контур не удалось. Проверьте значения и состояние устройств.");
      }
    } finally {
      this._busy = false;
      this._setWizardBusy(false);
      this._render();
    }
  }

  async _resetWizardAfterConflict() {
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
    this._notice = "Настройки изменились в другом окне. Данные обновлены, откройте мастер и повторите действие.";
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
      const card = el("div", "card");
      card.appendChild(el("h3", null, contour.name));
      this._row(card, "Статус", this._names("contour_statuses", contour.status));
      this._row(card, "Режим", this._names("contour_modes", contour.mode));
      if (contour.schedule && contour.schedule.enabled) {
        const next = contour.schedule.next_profile
          ? `${this._profileName(contour.schedule.next_profile)} · ${contour.schedule.next_change_at || ""}`
          : "Расписание включено";
        this._row(card, "Расписание", next);
      }
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
      card.appendChild(applyButton);

      const temporary = execution.temporary_temperature || {};
      (contour.rooms || []).forEach((room) => {
        const block = el("div");
        block.appendChild(el("div", "muted", room.name || room.id));
        const input = el("input");
        input.type = "number";
        input.min = temporary.minimum;
        input.max = temporary.maximum;
        input.step = temporary.step;
        const current = room.temporary_temperature && room.temporary_temperature.active
          ? room.temporary_temperature.temperature
          : room.targets && room.targets.temperature;
        input.value = current;
        block.appendChild(input);
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
        block.appendChild(setButton);
        if (room.temporary_temperature && room.temporary_temperature.active) {
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
          block.appendChild(clearButton);
          block.appendChild(
            el("div", "muted", `Действует временная температура ${room.temporary_temperature.temperature} °C`)
          );
        }
        card.appendChild(block);
      });
      container.appendChild(card);
    });
  }

  _renderProfiles(container, setup) {
    container.innerHTML = "";
    if (!setup) {
      container.appendChild(el("h2", null, "Профили «День» и «Ночь»"));
      container.appendChild(el("div", "card empty-state muted", "Настройки профилей временно недоступны."));
      return;
    }
    container.appendChild(el("h2", null, "Профили «День» и «Ночь»"));
    container.appendChild(el(
      "div",
      "section-intro",
      "Комфортные цели каждой комнаты для дневного и ночного периодов."
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
    const grid = el("div");
    (setup.rooms || []).forEach((room) => {
      const card = el("article", "card profile-room");
      card.appendChild(el("h3", null, room.name || room.id));
      const columns = el("div", "profile-columns");
      const roomError = el("div", "field-error");
      fields[room.id] = { error: roomError };
      ["day", "night"].forEach((profile) => {
        const values = (room.profiles && room.profiles[profile]) || {};
        const title = (setup.display_names && setup.display_names.profiles
          && setup.display_names.profiles[profile]) || profile;
        const profileBlock = el("div", "profile-block");
        profileBlock.appendChild(el("h4", null, title));
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
        "Профили сохранены.",
        "Настройки изменились в другом окне. Данные обновлены, повторите сохранение."
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
    const card = el("div", "card");
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
    card.appendChild(dayRow);
    const nightStart = el("input");
    nightStart.type = "time";
    nightStart.value = schedule.night_start || "23:00";
    const nightRow = el("label", "form-field", "Начало ночи");
    nightRow.appendChild(nightStart);
    card.appendChild(nightRow);
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
        enabled ? "Расписание сохранено и включено." : "Расписание сохранено.",
        "Настройки изменились в другом окне. Данные обновлены, повторите сохранение."
      );
    });
    const actions = el("div", "actions");
    actions.appendChild(saveButton);
    card.appendChild(actions);
    container.appendChild(card);
  }

  _signalCandidateType(candidate, signalKind) {
    const deviceClassLabels = {
      temperature: "Датчик температуры",
      motion: "Датчик движения",
      occupancy: "Датчик занятости",
      presence: "Датчик присутствия",
      window: "Датчик окна",
      door: "Датчик двери",
      opening: "Датчик открытия",
      garage_door: "Датчик ворот",
      heat: "Датчик нагрева",
      running: "Датчик работы",
      power: "Датчик питания",
    };
    if (candidate.domain === "weather") return "Погодный сервис";
    if (candidate.domain === "person") return "Человек";
    if (candidate.domain === "device_tracker") return "Трекер";
    if (candidate.domain === "switch") return "Выключатель";
    if (candidate.domain === "input_boolean") return "Логический переключатель";
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

  _singleChoicePicker({
    title, helper, candidates, current, signalKind, onChange, groupByRoom = true,
  }) {
    const fieldset = el("fieldset", "signal-picker");
    fieldset.appendChild(el("legend", null, title));
    if (helper) fieldset.appendChild(el("div", "muted signal-picker-help", helper));
    const search = el("input", "entity-search");
    search.type = "search";
    search.placeholder = "Найти подходящее устройство";
    setAttr(search, "aria-label", `Поиск: ${title.toLocaleLowerCase("ru")}`);
    fieldset.appendChild(search);
    const list = el("div", "signal-picker-list");
    fieldset.appendChild(list);
    const radioName = requestId("signal");
    const radios = [];
    const optionNodes = [];
    const groups = new Map();

    const addRadio = (container, candidate, value, label, meta) => {
      const radio = el("input");
      radio.type = "radio";
      radio.name = radioName;
      radio.value = value;
      radio.checked = current ? value === current : value === "";
      const option = el("label", "signal-option");
      if (radio.checked) option.className += " is-selected";
      option.appendChild(radio);
      if (candidate && candidate.image_url
          && ZIGBEE2MQTT_IMAGE_PATTERN.test(candidate.image_url)) {
        option.className += " has-thumb";
        const thumb = el("span", "signal-option-thumb");
        const image = el("img");
        image.src = candidate.image_url;
        image.alt = "";
        setAttr(image, "loading", "lazy");
        setAttr(image, "decoding", "async");
        setAttr(image, "referrerpolicy", "no-referrer");
        image.addEventListener("error", () => { thumb.hidden = true; });
        thumb.appendChild(image);
        option.appendChild(thumb);
      }
      const identity = el("span", "entity-label");
      identity.appendChild(el("strong", null, label));
      if (meta) identity.appendChild(el("small", null, meta));
      option.appendChild(identity);
      radio.addEventListener("change", () => {
        if (!radio.checked) return;
        radios.forEach((peer) => {
          peer.radio.checked = peer.radio === radio;
          peer.option.className = [
            "signal-option",
            peer.hasThumb ? "has-thumb" : "",
            peer.radio.checked ? "is-selected" : "",
          ].filter(Boolean).join(" ");
        });
        onChange();
      });
      container.appendChild(option);
      radios.push({ radio, option, hasThumb: option.className.includes("has-thumb") });
      if (candidate) {
        optionNodes.push({
          node: option,
          group: container,
          searchText: normalizedText([
            candidate.name,
            candidate.entity_id,
            candidate.room_name,
            candidate.device_name,
            candidate.manufacturer,
            candidate.model,
            this._signalCandidateType(candidate, signalKind),
          ].join(" ")),
        });
      }
    };

    const noneGroup = el("div", "signal-type-options");
    addRadio(noneGroup, null, "", "Не привязано", "Источник можно выбрать позже");
    list.appendChild(noneGroup);
    this._candidateWithCurrent(candidates, current)
      .sort((left, right) => (
        Number(right.entity_id === current) - Number(left.entity_id === current)
        || String(left.room_name || "").localeCompare(String(right.room_name || ""), "ru")
        || this._signalCandidateType(left, signalKind)
          .localeCompare(this._signalCandidateType(right, signalKind), "ru")
        || String(left.name).localeCompare(String(right.name), "ru")
      ))
      .forEach((candidate) => {
        const roomLabel = candidate.domain === "weather"
          ? "Погодные сервисы"
          : (candidate.room_name || (candidate.room_id ? candidate.room_id : "Без комнаты"));
        const roomKey = groupByRoom ? roomLabel : "room";
        if (!groups.has(roomKey)) {
          const roomGroup = el("section", "signal-room-group");
          if (groupByRoom) roomGroup.appendChild(el("h4", null, roomLabel));
          const typeGroups = el("div", "signal-type-groups");
          roomGroup.appendChild(typeGroups);
          list.appendChild(roomGroup);
          groups.set(roomKey, { roomGroup, typeGroups, types: new Map() });
        }
        const roomGroup = groups.get(roomKey);
        const typeLabel = this._signalCandidateType(candidate, signalKind);
        if (!roomGroup.types.has(typeLabel)) {
          const typeGroup = el("div", "signal-type-group");
          typeGroup.appendChild(el("h5", null, typeLabel));
          const typeOptions = el("div", "signal-type-options");
          typeGroup.appendChild(typeOptions);
          roomGroup.typeGroups.appendChild(typeGroup);
          roomGroup.types.set(typeLabel, { typeGroup, typeOptions });
        }
        const typeGroup = roomGroup.types.get(typeLabel);
        const details = [
          candidate.entity_id,
          candidate.missing ? "ранее выбранная сущность сейчас недоступна" : "",
          candidate.available === false && !candidate.missing ? "сейчас недоступно" : "",
        ].filter(Boolean).join(" · ");
        addRadio(
          typeGroup.typeOptions,
          candidate,
          candidate.entity_id,
          candidate.device_name || candidate.name || candidate.entity_id,
          details
        );
      });
    if (!optionNodes.length) {
      list.appendChild(el("div", "muted", "Подходящих устройств пока не найдено."));
    }
    search.addEventListener("input", () => {
      const query = normalizedText(search.value);
      optionNodes.forEach((option) => {
        option.node.hidden = Boolean(query) && !option.searchText.includes(query);
      });
      groups.forEach((roomGroup) => {
        roomGroup.types.forEach(({ typeGroup, typeOptions }) => {
          typeGroup.hidden = Array.from(typeOptions.children).every((node) => node.hidden);
        });
        roomGroup.roomGroup.hidden = Array.from(roomGroup.types.values())
          .every(({ typeGroup }) => typeGroup.hidden);
      });
    });
    return {
      root: fieldset,
      value: () => {
        const selected = radios.find(({ radio }) => radio.checked);
        return selected ? selected.radio.value : "";
      },
      radios,
    };
  }

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
    const card = el("div", "card");
    const values = home.home || {};
    const candidates = home.candidates || {};
    const bindings = [
      {
        key: "outdoor_temperature_entity_id",
        label: "Наружная температура",
        helper: "Можно выбрать уличный датчик температуры или погодный сервис Home Assistant.",
        signalKind: "outdoor_temperature",
        options: candidates.outdoor_temperature || [],
      },
      {
        key: "presence_entity_id",
        label: "Общее присутствие дома",
        helper: "Только люди, трекеры и датчики движения, занятости или присутствия.",
        signalKind: "presence",
        options: candidates.presence || [],
      },
      {
        key: "central_heating_entity_id",
        label: "Центральное отопление",
        helper: "Выключатель отопления или подходящий датчик его работы.",
        signalKind: "central_heating",
        options: candidates.central_heating || [],
      },
    ];
    const pickers = {};
    bindings.forEach((binding) => {
      const picker = this._singleChoicePicker({
        title: binding.label,
        helper: binding.helper,
        candidates: binding.options,
        current: values[binding.key],
        signalKind: binding.signalKind,
        onChange: () => {
          this._markDirty("home", dirtyNotice);
        },
      });
      card.appendChild(picker.root);
      pickers[binding.key] = picker;
    });
    const high = numberField(
      values.heating_lockout_high, -40, 60, 0.5,
      () => this._markDirty("home", dirtyNotice)
    );
    const highRow = el("label", "form-field", "Блокировка отопления выше, °C");
    highRow.appendChild(high);
    card.appendChild(highRow);
    const low = numberField(
      values.heating_lockout_low, -40, 60, 0.5,
      () => this._markDirty("home", dirtyNotice)
    );
    const lowRow = el("label", "form-field", "Разблокировка отопления ниже, °C");
    lowRow.appendChild(low);
    card.appendChild(lowRow);
    card.appendChild(
      el("div", "muted", "Пороги допустимы от −40 до 60 °C; нижний должен быть строго меньше верхнего.")
    );
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
      if (
        rawHigh === "" || rawLow === ""
        || !Number.isFinite(highValue) || !Number.isFinite(lowValue)
        || highValue < -40 || highValue > 60 || lowValue < -40 || lowValue > 60
        || lowValue >= highValue
      ) {
        validationError.textContent =
          "Проверьте пороги: от -40 до 60 °C, нижний строго меньше верхнего.";
        this._activateSection("climate");
        focusNode(
          rawHigh === "" || !Number.isFinite(highValue) || highValue < -40 || highValue > 60
            ? high : low
        );
        return;
      }
      this._save(
        "home",
        HOME_API,
        {
          outdoor_temperature_entity_id:
            pickers.outdoor_temperature_entity_id.value() || null,
          presence_entity_id: pickers.presence_entity_id.value() || null,
          central_heating_entity_id:
            pickers.central_heating_entity_id.value() || null,
          heating_lockout_high: highValue,
          heating_lockout_low: lowValue,
        },
        "Сохранить привязки сигналов дома и пороги блокировки отопления?",
        "Сигналы дома сохранены.",
        "Настройки изменились в другом окне. Данные обновлены, повторите сохранение."
      );
    });
    const actions = el("div", "actions");
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
      "Окно - одиночная привязка. Комнатное присутствие - набор датчиков и пока не меняет температуру мгновенно: для этого нужна отдельная политика занятости."
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
    const grid = el("div", "room-card-grid");
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
        candidates: roomWindows,
        current: room.window_entity_id,
        signalKind: "window",
        groupByRoom: false,
        onChange: () => {
          this._markDirty("windows", dirtyNotice);
        },
      });
      block.appendChild(windowPicker.root);
      block.appendChild(el("h4", null, "Датчики присутствия"));
      block.appendChild(
        el("div", "muted", "Можно выбрать несколько датчиков движения или занятости; один датчик относится только к одной комнате.")
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
        occupancy: "Занятость",
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
      let failed = false;
      try {
        await this._hass.callApi("POST", WINDOWS_API, {
          rooms: changed.map((roomId) => ({
            room_id: roomId,
            window_entity_id: fields[roomId].windowPicker.value() || null,
            presence_entity_ids: selectedPresence(roomId),
          })),
        });
      } catch (error) {
        failed = true;
      }
      this._dirty.windows = false;
      this._busy = false;
      this._notice = failed
        ? "Сохранить сигналы комнат не удалось. Данные обновлены, проверьте значения."
        : "Сигналы комнат сохранены.";
      await this._load();
    });
    container.appendChild(dirtyNotice);
    const actions = el("div", "actions");
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

  _renderScenarios(container) {
    container.innerHTML = "";
    const card = el("div", "card");
    card.appendChild(el("h2", null, "Сценарии"));
    if (this._scenarios.loading && !this._scenarios.list) {
      card.appendChild(el("div", "muted", "Загрузка сценариев…"));
      container.appendChild(card);
      return;
    }
    if (!this._scenarios.list || !this._scenarios.list.scenarios) {
      card.appendChild(el("div", "muted", "Список сценариев недоступен."));
      container.appendChild(card);
      return;
    }
    const items = this._scenarios.list.scenarios;
    if (!items.length) {
      card.appendChild(el("div", "muted", "Нет сохранённых сценариев."));
    }
    items.forEach((scenario) => {
      const row = el("div", "row");
      row.style.alignItems = "center";
      const title = el("span", null, `${scenario.icon || "mdi:script"} ${scenario.title || scenario.id}`);
      row.appendChild(title);
      const actions = el("span");
      actions.style.display = "flex";
      actions.style.gap = "8px";
      if (scenario.enabled) {
        const runBtn = el("button", null, "Запустить");
        runBtn.addEventListener("click", () => this._post(SCENARIOS_RUN_API, { scenario_id: scenario.id }, scenario.requires_confirmation ? `Запустить сценарий "${scenario.title}"?` : null));
        actions.appendChild(runBtn);
      }
      const testBtn = el("button", "secondary", "Проверить");
      testBtn.addEventListener("click", () => this._scenarioTest(scenario));
      actions.appendChild(testBtn);
      const delBtn = el("button", "secondary", "Удалить");
      delBtn.addEventListener("click", () => this._post(SCENARIOS_DELETE_API, { scenario_id: scenario.id }, `Удалить сценарий "${scenario.title}"?`));
      actions.appendChild(delBtn);
      row.appendChild(actions);
      card.appendChild(row);
    });
    container.appendChild(card);
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
      this._notice = "Проверить сценарий не удалось.";
    } finally {
      this._busy = false;
    }
    this._render();
  }

  _renderSettings(container) {
    container.innerHTML = "";
    const card = el("div", "card");
    card.appendChild(el("h2", null, "Подключение"));
    const modeLabel = el("label", "form-field", "Режим подключения");
    const modeSelect = el("select");
    [
      { value: "center", label: "Центр умного дома" },
      { value: "home_assistant", label: "Home Assistant" },
    ].forEach((opt) => {
      const option = el("option", null, opt.label);
      option.value = opt.value;
      modeSelect.appendChild(option);
    });
    modeSelect.value = this._settingsData.connection_mode;
    modeSelect.addEventListener("change", () => {
      this._settingsData.connection_mode = modeSelect.value;
      this._settingsDirty = true;
      this._render();
    });
    modeLabel.appendChild(modeSelect);
    card.appendChild(modeLabel);
    const centerLabel = el("label", "form-field", "Адрес Центра умного дома");
    const centerInput = el("input");
    centerInput.type = "text";
    centerInput.value = this._settingsData.smart_home_center_url;
    centerInput.addEventListener("input", () => {
      this._settingsData.smart_home_center_url = centerInput.value;
      this._settingsDirty = true;
      this._render();
    });
    centerLabel.appendChild(centerInput);
    card.appendChild(centerLabel);
    const haLabel = el("label", "form-field", "Адрес Home Assistant");
    const haInput = el("input");
    haInput.type = "text";
    haInput.value = this._settingsData.home_assistant_url;
    haInput.addEventListener("input", () => {
      this._settingsData.home_assistant_url = haInput.value;
      this._settingsDirty = true;
      this._render();
    });
    haLabel.appendChild(haInput);
    card.appendChild(haLabel);
    const saveBtn = el("button", null, "Сохранить");
    saveBtn.disabled = !this._settingsDirty;
    saveBtn.addEventListener("click", () => this._saveSettings());
    card.appendChild(saveBtn);
    container.appendChild(card);
  }

  async _saveSettings() {
    if (this._busy || !this._settingsDirty) return;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      await this._hass.callApi("POST", CONNECTION_SETTINGS_API, this._settingsData);
      this._settingsDirty = false;
      this._notice = "Настройки подключения сохранены.";
      this._error = false;
    } catch (error) {
      this._notice = "Не удалось сохранить настройки подключения.";
    } finally {
      this._busy = false;
    }
    await this._load();
  }
}

customElements.define("hausman-hub-panel", HausmanHubPanel);
