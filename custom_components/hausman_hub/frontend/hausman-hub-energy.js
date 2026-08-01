/* Energy presentation shared by the overview card and the dedicated section. */

const number = (value, digits = 1) => Number.isFinite(Number(value))
  ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(Number(value))
  : "—";

function sourceMetric(source, key, unit, digits = 1) {
  return source && source[key] !== null && source[key] !== undefined
    ? `${number(source[key], digits)} ${unit}` : "—";
}

function selectedSources(energy) {
  const selected = new Set(Array.isArray(energy && energy.selectedSourceIds)
    ? energy.selectedSourceIds : []);
  return (energy && Array.isArray(energy.sources) ? energy.sources : [])
    .filter((source) => selected.has(source.id));
}

function primaryEnergyValue(energy) {
  const mode = energy && energy.settings && energy.settings.displayUnits || "watts";
  const watts = energy && energy.currentPowerW !== null && energy.currentPowerW !== undefined
    ? `${number(energy.currentPowerW)} Вт` : "—";
  const amps = energy && energy.currentA !== null && energy.currentA !== undefined
    ? `${number(energy.currentA, 2)} А` : "—";
  if (mode === "amps") return amps;
  if (mode === "both") return `${watts} · ${amps}`;
  return watts;
}

function energyMetric(deps, label, value, tone = "", caption = "") {
  const { el } = deps;
  const item = el("div", `energy-metric${tone ? ` ${tone}` : ""}`);
  item.appendChild(el("span", null, label));
  item.appendChild(el("strong", null, value));
  if (caption) item.appendChild(el("small", null, caption));
  return item;
}

function deviceWord(count) {
  const value = Math.abs(Number(count)) % 100;
  const tail = value % 10;
  if (value > 10 && value < 20) return "устройств";
  if (tail === 1) return "устройство";
  if (tail > 1 && tail < 5) return "устройства";
  return "устройств";
}

function sourceDevice(panel, source) {
  const devices = panel._homeDashboard && Array.isArray(panel._homeDashboard.devices)
    ? panel._homeDashboard.devices : [];
  return devices.find((item) => item.id === source.deviceId || item.physicalId === source.deviceId);
}

function energyDeviceVisual(panel, source, deps) {
  const { el, svgIcon, setAttr } = deps;
  const device = sourceDevice(panel, source);
  const visual = el("span", "energy-device-visual");
  const imageUrl = String(source.imageUrl || device && device.imageUrl || "");
  const fallback = el("span", "energy-device-visual-fallback");
  fallback.appendChild(svgIcon("energy"));
  if (/^https?:\/\//i.test(imageUrl)) {
    const image = el("img");
    image.src = imageUrl;
    image.alt = "";
    setAttr(image, "loading", "lazy");
    setAttr(image, "decoding", "async");
    setAttr(image, "referrerpolicy", "no-referrer");
    fallback.hidden = true;
    image.addEventListener("error", () => { image.hidden = true; fallback.hidden = false; });
    visual.appendChild(image);
  }
  visual.appendChild(fallback);
  return visual;
}

export function renderEnergyOverviewCard(panel, container, deps) {
  const { el, svgIcon, setAttr } = deps;
  const energy = panel._homeDashboard && panel._homeDashboard.energy;
  const card = el("button", "energy-overview-card");
  card.type = "button";
  setAttr(card, "aria-label", "Открыть подробный раздел энергии");
  card.addEventListener("click", () => panel._activateSection("energy"));
  const icon = el("span", "energy-overview-icon");
  icon.appendChild(svgIcon("energy"));
  card.appendChild(icon);
  const copy = el("span", "energy-overview-copy");
  copy.appendChild(el("span", "assistant-field-label", "Энергия сейчас"));
  copy.appendChild(el("strong", null, energy && energy.available ? primaryEnergyValue(energy) : "Нет данных"));
  const voltage = energy && energy.settings && energy.settings.showVoltage
    ? ` · ${sourceMetric(energy, "voltageV", "В")}` : "";
  copy.appendChild(el("small", null, `${selectedSources(energy).length} источников${voltage}`));
  card.appendChild(copy);
  card.appendChild(el("span", "energy-overview-chevron", "›"));
  container.appendChild(card);
}

function historyBars(panel, source, deps) {
  const { el, setAttr } = deps;
  const wrap = el("div", "energy-history");
  const history = panel._energyHistory && panel._energyHistory[source.id];
  const period = panel._energyHistoryPeriod || "day";
  const periodLabels = {
    day: ["за последние 24 часа", "Почасовая средняя мощность · последние 24 часа"],
    week: ["за последние 7 дней", "Почасовая средняя мощность · последние 7 дней"],
    month: ["за последний месяц", "Средняя мощность по дням · последний месяц"],
    year: ["за последний год", "Средняя мощность по дням · последний год"],
  };
  const values = Array.isArray(history) ? history
    .map((point) => Number(point.mean))
    .filter(Number.isFinite) : [];
  if (!values.length) {
    wrap.appendChild(el("div", "energy-history-empty", "История мощности пока недоступна. Текущие показания продолжают обновляться."));
    return wrap;
  }
  const max = Math.max(...values, 1);
  const chart = el("div", "energy-history-bars");
  setAttr(chart, "role", "img");
  setAttr(chart, "aria-label", `График мощности ${source.name} ${periodLabels[period][0]}`);
  const bucketSize = Math.max(1, Math.ceil(values.length / 48));
  const visibleValues = [];
  for (let index = 0; index < values.length; index += bucketSize) {
    const bucket = values.slice(index, index + bucketSize);
    visibleValues.push(bucket.reduce((sum, value) => sum + value, 0) / bucket.length);
  }
  visibleValues.forEach((value) => {
    const bar = el("span", "energy-history-bar");
    bar.style.height = `${Math.max(4, (value / max) * 100)}%`;
    setAttr(bar, "title", `${number(value)} Вт`);
    chart.appendChild(bar);
  });
  wrap.appendChild(chart);
  wrap.appendChild(el("div", "energy-chart-caption", periodLabels[period][1]));
  return wrap;
}

function renderDeviceDetail(panel, container, source, deps) {
  const { el, svgIcon } = deps;
  const back = el("button", "secondary energy-back", "← Все устройства");
  back.type = "button";
  back.addEventListener("click", () => {
    panel._energySelectedDeviceId = null;
    panel._renderEnergySection(container);
  });
  container.appendChild(back);
  const hero = el("section", "card energy-device-hero");
  const head = el("div", "energy-device-head");
  const icon = el("span", "energy-device-icon");
  icon.appendChild(svgIcon("energy"));
  head.appendChild(icon);
  const copy = el("div");
  copy.appendChild(el("h2", null, source.name));
  copy.appendChild(el("p", "section-intro", source.roomName || "Без комнаты"));
  head.appendChild(copy);
  hero.appendChild(head);
  const metrics = el("div", "energy-metric-grid");
  metrics.appendChild(energyMetric(deps, "Мощность", sourceMetric(source, "currentPowerW", "Вт"), "is-accent"));
  metrics.appendChild(energyMetric(deps, "Ток", sourceMetric(source, "currentA", "А", 2)));
  metrics.appendChild(energyMetric(deps, "Напряжение", sourceMetric(source, "voltageV", "В")));
  metrics.appendChild(energyMetric(deps, "Счётчик", sourceMetric(source, "totalKwh", "кВт·ч", 3)));
  hero.appendChild(metrics);
  hero.appendChild(historyBars(panel, source, deps));
  container.appendChild(hero);
  const device = panel._homeDashboard.devices.find((item) => item.id === source.deviceId);
  if (device) {
    const management = el("section", "energy-device-management");
    management.appendChild(el("h3", null, "Устройство и управление"));
    management.appendChild(el("p", "section-intro", "Все доступные функции одного физического устройства. Команда считается выполненной только после подтверждения состояния."));
    management.appendChild(panel._deviceInventoryCard(device));
    container.appendChild(management);
  }
}

function renderEnergyHistory(panel, energy, selected, deps) {
  const { el, setAttr } = deps;
  const card = el("section", "card energy-history-card");
  const head = el("div", "energy-card-head energy-history-head");
  const copy = el("div", "energy-card-title");
  const activePeriod = panel._energyHistoryPeriod || "day";
  const periodTitles = {
    day: "Потребление за 24 часа",
    week: "Потребление за 7 дней",
    month: "Потребление за месяц",
    year: "Потребление за год",
  };
  copy.appendChild(el("h3", null, periodTitles[activePeriod]));
  copy.appendChild(el("small", null, selected.length
    ? selected.map((item) => item.name).join(" + ") : "Источники не выбраны"));
  head.appendChild(copy);
  const periods = el("div", "energy-periods");
  [["day", "День"], ["week", "Неделя"], ["month", "Месяц"], ["year", "Год"]]
    .forEach(([value, label]) => {
    const selectedPeriod = activePeriod === value;
    const period = el("button", selectedPeriod ? "is-selected" : "", label);
    period.type = "button";
    period.disabled = panel._energyHistoryLoading;
    setAttr(period, "aria-pressed", selectedPeriod ? "true" : "false");
    period.addEventListener("click", () => {
      if (value === panel._energyHistoryPeriod) return;
      panel._energyHistoryPeriod = value;
      panel._energyHistory = {};
      if (panel._energyHistoryLoading) panel._energyHistoryReloadRequested = true;
      panel._renderEnergySection(panel._shell.homeSections.energy);
      loadEnergyHistory(panel);
    });
    periods.appendChild(period);
  });
  head.appendChild(periods);
  card.appendChild(head);
  const layout = el("div", "energy-history-layout");
  layout.appendChild(historyBars(panel, { id: "selection", name: "выбранных источников" }, deps));
  const sources = el("div", "energy-current-sources");
  sources.appendChild(el("h4", null, "Источники"));
  selected.slice(0, 2).forEach((source) => {
    const row = el("button", "energy-current-source");
    row.type = "button";
    row.addEventListener("click", () => {
      panel._energySelectedDeviceId = source.id;
      panel._renderEnergySection(panel._shell.homeSections.energy);
    });
    const identity = el("span");
    identity.appendChild(el("strong", null, source.name));
    identity.appendChild(el("small", null, `${sourceMetric(source, "currentA", "А", 2)} · ${sourceMetric(source, "voltageV", "В")}`));
    row.appendChild(identity);
    row.appendChild(el("b", null, sourceMetric(source, "currentPowerW", "Вт")));
    sources.appendChild(row);
  });
  if (!selected.length) sources.appendChild(el("div", "energy-sources-empty", "Выберите источники"));
  layout.appendChild(sources);
  card.appendChild(layout);
  return card;
}

function renderEnergyDevices(panel, container, sources, deps) {
  const { el, setAttr } = deps;
  const card = el("section", "card energy-devices-panel");
  const head = el("div", "energy-card-head energy-devices-toolbar");
  const title = el("div", "energy-card-title");
  title.appendChild(el("h3", null, "Устройства энергии"));
  title.appendChild(el("small", null, `${sources.length} ${deviceWord(sources.length)} · одна строка на физическое устройство`));
  head.appendChild(title);
  const controls = el("div", "energy-device-controls");
  const activeFilter = panel._energyFilter || "all";
  [["all", "Все"], ["online", "На связи"], ["power", "По мощности"]].forEach(([value, label]) => {
    const button = el("button", activeFilter === value ? "is-selected" : "", label);
    button.type = "button";
    button.addEventListener("click", () => {
      panel._energyFilter = value;
      panel._renderEnergySection(container);
    });
    controls.appendChild(button);
  });
  const search = el("input", "energy-device-search");
  search.type = "search";
  search.placeholder = "Поиск";
  search.value = panel._energyQuery || "";
  setAttr(search, "aria-label", "Найти энергетическое устройство");
  search.addEventListener("input", () => {
    panel._energyQuery = search.value;
    panel._renderEnergySection(container);
  });
  controls.appendChild(search);
  head.appendChild(controls);
  card.appendChild(head);

  const query = String(panel._energyQuery || "").trim().toLocaleLowerCase("ru");
  let shown = sources.filter((source) => activeFilter !== "online" || source.available);
  if (query) shown = shown.filter((source) => `${source.name} ${source.roomName || ""}`.toLocaleLowerCase("ru").includes(query));
  if (activeFilter === "power") shown = [...shown].sort((left, right) => (Number(right.currentPowerW) || 0) - (Number(left.currentPowerW) || 0));
  const list = el("div", "energy-device-list");
  shown.forEach((source) => {
    const device = sourceDevice(panel, source);
    const row = el("button", `energy-device-card${source.available ? "" : " is-unavailable"}`);
    row.type = "button";
    setAttr(row, "aria-label", `Открыть устройство ${source.name}`);
    row.addEventListener("click", () => {
      panel._energySelectedDeviceId = source.id;
      panel._renderEnergySection(container);
    });
    row.appendChild(energyDeviceVisual(panel, source, deps));
    const identity = el("span", "energy-device-card-copy");
    identity.appendChild(el("strong", null, source.name));
    identity.appendChild(el("small", null, [source.roomName || "Без комнаты", device && device.manufacturer, device && device.model].filter(Boolean).join(" · ")));
    row.appendChild(identity);
    const live = el("span", "energy-device-value is-accent");
    live.appendChild(el("b", null, source.available ? sourceMetric(source, "currentPowerW", "Вт") : "—"));
    live.appendChild(el("small", null, source.available
      ? `${sourceMetric(source, "currentA", "А", 2)} · ${sourceMetric(source, "voltageV", "В")}`
      : "актуальных данных нет"));
    row.appendChild(live);
    const accumulated = el("span", "energy-device-value");
    accumulated.appendChild(el("b", null, sourceMetric(source, source.todayKwh !== null && source.todayKwh !== undefined ? "todayKwh" : "totalKwh", "кВт·ч", 2)));
    accumulated.appendChild(el("small", null, source.todayKwh !== null && source.todayKwh !== undefined ? "за сегодня" : "накоплено"));
    row.appendChild(accumulated);
    const isPoweredOff = source.available && source.powered === false;
    const statusTone = !source.available ? "is-offline" : (isPoweredOff ? "is-powered-off" : "is-online");
    const status = el("span", `energy-device-status ${statusTone}`);
    status.appendChild(el("strong", null, !source.available ? "Нет связи" : (isPoweredOff ? "Выключен" : "В сети")));
    status.appendChild(el("small", null, !source.available ? "проверьте устройство" : (isPoweredOff ? "питание отключено" : "обновляется")));
    row.appendChild(status);
    row.appendChild(el("span", "energy-device-open", "Подробнее"));
    row.appendChild(el("span", "energy-overview-chevron", "›"));
    list.appendChild(row);
  });
  if (!shown.length) list.appendChild(el("div", "energy-devices-empty", "Устройства не найдены"));
  card.appendChild(list);
  return card;
}

function compactEnergySettings(panel, container, energy, deps) {
  const { el, setAttr } = deps;
  const draft = panel._energyDraft || {
    displayUnits: energy.settings.displayUnits || "watts",
    showVoltage: energy.settings.showVoltage !== false,
    aggregation: energy.settings.aggregation || "combined",
    useAllDevices: energy.settings.useAllDevices !== false,
    selectedDeviceIds: [...(energy.selectedSourceIds || [])],
  };
  panel._energyDraft = draft;
  const card = el("section", "card energy-compact-settings");
  card.appendChild(el("h3", null, "Карточка на главной"));
  card.appendChild(el("p", null, "Единицы, источники и способ группировки"));
  card.appendChild(el("span", "energy-settings-label", "Единицы"));
  const units = el("div", "energy-segments");
  [["watts", "Вт"], ["amps", "А"], ["both", "Вт + А"]].forEach(([value, label]) => {
    const button = el("button", draft.displayUnits === value ? "is-selected" : "", label);
    button.type = "button";
    setAttr(button, "aria-pressed", draft.displayUnits === value ? "true" : "false");
    button.addEventListener("click", () => { draft.displayUnits = value; panel._renderEnergySection(container); });
    units.appendChild(button);
  });
  card.appendChild(units);
  [["Напряжение", draft.showVoltage ? "Показывать" : "Скрывать", () => { draft.showVoltage = !draft.showVoltage; }],
    ["Источники", draft.aggregation === "combined" ? "Вместе" : "Раздельно", () => { draft.aggregation = draft.aggregation === "combined" ? "separate" : "combined"; }],
    ["Все устройства", draft.useAllDevices ? "Выбраны" : "Выборочно", () => { draft.useAllDevices = !draft.useAllDevices; }],
  ].forEach(([label, value, update]) => {
    const row = el("button", "energy-setting-line");
    row.type = "button";
    row.appendChild(el("span", null, label));
    row.appendChild(el("strong", null, value));
    row.addEventListener("click", () => { update(); panel._renderEnergySection(container); });
    card.appendChild(row);
  });
  card.appendChild(el("span", "energy-settings-label", "Источники на карточке"));
  const sources = el("div", "energy-compact-sources");
  energy.sources.slice(0, 5).forEach((source) => {
    const checked = draft.useAllDevices || draft.selectedDeviceIds.includes(source.id);
    const label = el("label", "energy-compact-source");
    const checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.checked = checked;
    checkbox.disabled = draft.useAllDevices;
    checkbox.addEventListener("change", () => {
      const selected = new Set(draft.selectedDeviceIds);
      if (checkbox.checked) selected.add(source.id); else selected.delete(source.id);
      draft.selectedDeviceIds = [...selected];
    });
    label.appendChild(checkbox);
    const copy = el("span");
    copy.appendChild(el("strong", null, source.name));
    copy.appendChild(el("small", null, sourceMetric(source, "currentPowerW", "Вт")));
    label.appendChild(copy);
    sources.appendChild(label);
  });
  card.appendChild(sources);
  const save = el("button", "energy-settings-save", "Сохранить настройки");
  save.type = "button";
  save.disabled = panel._busy || (!draft.useAllDevices && !draft.selectedDeviceIds.length);
  save.addEventListener("click", () => panel._saveEnergySettings());
  card.appendChild(save);
  return card;
}

function renderEnergySidebar(panel, container, energy, sources, deps) {
  const { el, svgIcon } = deps;
  const sidebar = el("aside", "energy-sidebar");
  const summary = el("section", "card energy-sidebar-summary");
  summary.appendChild(el("h3", null, "Сводка"));
  summary.appendChild(el("strong", "energy-sidebar-primary", sourceMetric(energy, "currentPowerW", "Вт")));
  summary.appendChild(el("small", "energy-sidebar-caption", "Текущая нагрузка"));
  const history = panel._energyHistory && panel._energyHistory.selection;
  const peak = Array.isArray(history) && history.length
    ? `${number(Math.max(...history.map((point) => Number(point.mean) || 0)))} Вт` : "—";
  [["Сегодня", sourceMetric(energy, "todayKwh", "кВт·ч", 2), ""],
    ["Пик", peak, ""],
    ["Напряжение", sourceMetric(energy, "voltageV", "В"), ""],
  ].forEach(([label, value, tone]) => {
    const row = el("div", `energy-sidebar-row${tone ? ` ${tone}` : ""}`);
    row.appendChild(el("span", null, label));
    row.appendChild(el("strong", null, value));
    summary.appendChild(row);
  });
  sidebar.appendChild(summary);
  const unavailable = sources.filter((source) => !source.available);
  const attention = el("section", `card energy-attention${unavailable.length ? " has-warning" : ""}`);
  const attentionHead = el("div", "energy-attention-head");
  attentionHead.appendChild(svgIcon(unavailable.length ? "warning" : "shield"));
  attentionHead.appendChild(el("strong", null, unavailable.length ? "Требует внимания" : "Всё на связи"));
  attention.appendChild(attentionHead);
  if (unavailable.length) unavailable.slice(0, 2).forEach((source) => {
    const row = el("div", "energy-attention-row");
    row.appendChild(el("span", null, source.name));
    row.appendChild(el("strong", null, "Нет связи"));
    attention.appendChild(row);
  });
  else attention.appendChild(el("p", null, "Показания обновляются штатно"));
  sidebar.appendChild(attention);
  sidebar.appendChild(compactEnergySettings(panel, container, energy, deps));
  return sidebar;
}

export function renderEnergySection(panel, container, deps) {
  const { el, svgIcon, setAttr } = deps;
  container.innerHTML = "";
  const energy = panel._homeDashboard && panel._homeDashboard.energy;
  if (!energy) {
    container.appendChild(el("div", "card empty-state", "Данные энергии пока недоступны."));
    return;
  }
  const source = energy.sources.find((item) => item.id === panel._energySelectedDeviceId);
  if (source) {
    renderDeviceDetail(panel, container, source, deps);
    return;
  }
  const heading = el("div", "home-section-heading energy-section-heading");
  const copy = el("div");
  copy.appendChild(el("h2", null, "Энергия"));
  copy.appendChild(el("p", "section-intro", "Потребление, нагрузка и управление источниками"));
  heading.appendChild(copy);
  container.appendChild(heading);
  const pageLayout = el("div", "energy-page-layout");
  const mainColumn = el("div", "energy-main-column");
  const selected = selectedSources(energy);
  const allSources = energy.sources;
  const hero = el("section", "card energy-live-card");
  const liveHead = el("div", "energy-card-head energy-live-head");
  liveHead.appendChild(el("h3", null, "Энергия сейчас"));
  const sourcesButton = el("button", "energy-sources-button", `Источники: ${selected.length} ${deviceWord(selected.length)}`);
  sourcesButton.type = "button";
  sourcesButton.addEventListener("click", () => {
    const settings = container.querySelector && container.querySelector(".energy-compact-settings");
    if (settings && typeof settings.scrollIntoView === "function") settings.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  liveHead.appendChild(sourcesButton);
  hero.appendChild(liveHead);
  const metrics = el("div", "energy-metric-grid");
  metrics.appendChild(energyMetric(deps, "Мощность", sourceMetric(energy, "currentPowerW", "Вт"), "is-accent", "Сейчас · выбранные источники"));
  metrics.appendChild(energyMetric(deps, "Ток", sourceMetric(energy, "currentA", "А", 2), "", "Суммарно по источникам"));
  metrics.appendChild(energyMetric(deps, "Напряжение", sourceMetric(energy, "voltageV", "В"), "", Number(energy.voltageV) >= 207 && Number(energy.voltageV) <= 253 ? "Нормальный диапазон" : "Проверьте напряжение"));
  metrics.appendChild(energyMetric(deps, "Сегодня", sourceMetric(energy, "todayKwh", "кВт·ч", 2), "is-success", energy.todayKwh === null || energy.todayKwh === undefined ? "История не передана" : "За текущие сутки"));
  hero.appendChild(metrics);
  mainColumn.appendChild(hero);
  mainColumn.appendChild(renderEnergyHistory(panel, energy, selected, deps));
  mainColumn.appendChild(renderEnergyDevices(panel, container, allSources, deps));
  pageLayout.appendChild(mainColumn);
  pageLayout.appendChild(renderEnergySidebar(panel, container, energy, allSources, deps));
  container.appendChild(pageLayout);
}

export async function loadEnergyHistory(panel) {
  if (!panel._hass || panel._energyHistoryLoading) return;
  const energy = panel._homeDashboard && panel._homeDashboard.energy;
  if (!energy || !Array.isArray(energy.sources)) return;
  if (typeof panel._hass.callApi !== "function") return;
  panel._energyHistoryLoading = true;
  try {
    const period = panel._energyHistoryPeriod || "day";
    const ranges = {
      day: { days: 1, interval: "1h" },
      week: { days: 7, interval: "1h" },
      month: { days: 31, interval: "1d" },
      year: { days: 365, interval: "1d" },
    };
    const range = ranges[period] || ranges.day;
    const end = new Date();
    const start = new Date(end.getTime() - range.days * 24 * 60 * 60 * 1000);
    const params = new URLSearchParams({
      from: start.toISOString(),
      to: end.toISOString(),
      interval: range.interval,
    });
    energy.sources.forEach((source) => params.append("deviceId", source.deviceId));
    const response = await panel._hass.callApi(
      "GET", `hausman_hub/v1/energy/history?${params.toString()}`,
    );
    const history = {};
    (response && Array.isArray(response.series) ? response.series : [])
      .filter((series) => series.unit === "W" && series.metric === "power")
      .forEach((series) => {
        const source = energy.sources.find((item) => item.deviceId === series.deviceId);
        const key = series.scope === "selection" ? "selection" : (source && source.id || series.deviceId);
        history[key] = (series.points || []).map((point) => ({
          start: point.at,
          mean: point.value,
        }));
      });
    panel._energyHistory = history;
  } catch (error) {
    panel._energyHistory = panel._energyHistory || {};
    panel._energyHistoryError = error && error.message || "history_unavailable";
  } finally {
    panel._energyHistoryLoading = false;
    if (panel._energyHistoryReloadRequested) {
      panel._energyHistoryReloadRequested = false;
      loadEnergyHistory(panel);
      return;
    }
    if (panel._activeSection === "energy") panel._render();
  }
}

export async function saveEnergySettings(panel) {
  const path = "hausman_hub/v1/energy-settings";
  const current = await panel._hass.callApi("GET", path);
  return panel._hass.callApi("PUT", path, {
    expectedRevision: current.revision,
    settings: panel._energyDraft,
  });
}
