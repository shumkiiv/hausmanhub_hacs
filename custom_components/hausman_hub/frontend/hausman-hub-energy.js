import { renderEnergyHistoryChart } from "./hausman-hub-energy-chart.js?v=1.52.19";

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

function energyPowerAction(panel, source, actionId) {
  const device = sourceDevice(panel, source);
  const targets = device ? panel._catalogTargets(device) : [];
  return targets.flatMap((target) => (target.actions || [])
    .map((action) => ({ target, action })))
    .find((entry) => entry.action.action_id === actionId
      && !(entry.action.allowed_fields || []).includes("value"));
}

function runEnergyPowerAction(panel, source, actionId) {
  const item = energyPowerAction(panel, source, actionId);
  if (!item || !source.available || panel._busy) return;
  const device = sourceDevice(panel, source);
  const breaker = /автомат|breaker|rcbo|mcb|din/i.test(`${source.name} ${device && device.model || ""}`);
  if (actionId === "turn_off" && breaker
      && !window.confirm(`Отключить «${source.name}»? Питание подключённой линии будет снято.`)) return;
  panel._executeDeviceAction(item.target.target_id, actionId, null);
}

function showEnergyDevice(panel, container, sourceId) {
  panel._energySelectedDeviceId = sourceId;
  panel._renderEnergySection(container);
  if (typeof container.scrollIntoView === "function") container.scrollIntoView({ block: "start" });
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

function energyPeriodButtons(panel, container, deps) {
  const { el, setAttr } = deps;
  const periods = el("div", "energy-history-controls");
  const metrics = el("div", "energy-periods energy-history-metrics");
  [["power", "Мощность"], ["energy", "Расход"]].forEach(([value, label]) => {
    const selected = (panel._energyHistoryMetric || "power") === value;
    const button = el("button", selected ? "is-selected" : "", label);
    button.type = "button"; setAttr(button, "aria-pressed", selected ? "true" : "false");
    button.addEventListener("click", () => { panel._energyHistoryMetric = value; panel._renderEnergySection(container); });
    metrics.appendChild(button);
  });
  periods.appendChild(metrics);
  const range = el("div", "energy-periods energy-history-ranges");
  [["day", "День"], ["week", "Неделя"], ["month", "Месяц"], ["year", "Год"]]
    .forEach(([value, label]) => {
      const selected = (panel._energyHistoryPeriod || "day") === value;
      const button = el("button", selected ? "is-selected" : "", label);
      button.type = "button";
      button.disabled = panel._energyHistoryLoading;
      setAttr(button, "aria-pressed", selected ? "true" : "false");
      button.addEventListener("click", () => {
        if (selected) return;
        panel._energyHistoryPeriod = value;
        panel._energyHistory = {};
        if (panel._energyHistoryLoading) panel._energyHistoryReloadRequested = true;
        panel._renderEnergySection(container);
        loadEnergyHistory(panel);
      });
      range.appendChild(button);
    });
  periods.appendChild(range);
  return periods;
}

function renderDeviceDetail(panel, container, source, deps) {
  const { el } = deps;
  const device = sourceDevice(panel, source);
  const back = el("button", "secondary energy-back", "← Все устройства");
  back.type = "button";
  back.addEventListener("click", () => showEnergyDevice(panel, container, null));
  container.appendChild(back);
  const layout = el("div", "energy-detail-layout");
  const main = el("div", "energy-detail-main");
  const hero = el("section", "card energy-device-hero");
  const head = el("div", "energy-device-head");
  head.appendChild(energyDeviceVisual(panel, source, deps));
  const copy = el("div", "energy-device-identity");
  copy.appendChild(el("h2", null, source.name));
  copy.appendChild(el("p", "section-intro", [source.roomName || "Без комнаты", device && device.manufacturer, device && device.model].filter(Boolean).join(" · ")));
  head.appendChild(copy);
  const status = el("span", `energy-detail-status ${!source.available ? "is-offline" : (source.powered === false ? "is-off" : "is-online")}`,
    !source.available ? "Нет связи" : (source.powered === false ? "Питание выключено" : "Работает"));
  head.appendChild(status);
  hero.appendChild(head);
  const metrics = el("div", "energy-metric-grid");
  metrics.appendChild(energyMetric(deps, "Мощность", sourceMetric(source, "currentPowerW", "Вт"), "is-accent", "Текущая нагрузка"));
  metrics.appendChild(energyMetric(deps, "Ток", sourceMetric(source, "currentA", "А", 2), "", "Сейчас"));
  metrics.appendChild(energyMetric(deps, "Напряжение", sourceMetric(source, "voltageV", "В"), "", Number(source.voltageV) >= 207 && Number(source.voltageV) <= 253 ? "В норме" : "Проверьте сеть"));
  metrics.appendChild(energyMetric(deps, "Счётчик", sourceMetric(source, "totalKwh", "кВт·ч", 3), "", "Накоплено"));
  hero.appendChild(metrics);
  main.appendChild(hero);
  const chartCard = el("section", "card energy-device-chart-card");
  const chartHead = el("div", "energy-card-head energy-history-head");
  const chartCopy = el("div", "energy-card-title");
  chartCopy.appendChild(el("h3", null, (panel._energyHistoryMetric || "power") === "energy" ? "История расхода" : "История мощности"));
  chartCopy.appendChild(el("small", null, "Фактические данные Recorder Home Assistant"));
  chartHead.appendChild(chartCopy);
  chartHead.appendChild(energyPeriodButtons(panel, container, deps));
  chartCard.appendChild(chartHead);
  chartCard.appendChild(renderEnergyHistoryChart(panel, source, deps, () => {
    panel._energyHistoryError = null;
    loadEnergyHistory(panel);
  }));
  main.appendChild(chartCard);
  layout.appendChild(main);

  const sidebar = el("aside", "energy-detail-sidebar");
  const control = el("section", "card energy-device-control-card");
  control.appendChild(el("h3", null, "Питание"));
  control.appendChild(el("p", null, source.available
    ? "Команда будет подтверждена по фактическому состоянию устройства."
    : "Управление недоступно, пока устройство не вернётся в сеть."));
  const controls = el("div", "energy-power-actions");
  let controlCount = 0;
  [["turn_on", "Включить"], ["turn_off", "Отключить"]].forEach(([actionId, label]) => {
    const item = energyPowerAction(panel, source, actionId);
    if (!item) return;
    const button = el("button", actionId === "turn_off" ? "secondary is-danger" : "secondary", label);
    button.type = "button";
    button.disabled = panel._busy || !source.available;
    button.addEventListener("click", () => runEnergyPowerAction(panel, source, actionId));
    controls.appendChild(button);
    controlCount += 1;
  });
  if (!controlCount) controls.appendChild(el("span", "energy-control-unavailable", "Для устройства доступен только просмотр показаний."));
  control.appendChild(controls);
  sidebar.appendChild(control);
  const info = el("section", "card energy-device-info-card");
  info.appendChild(el("h3", null, "Об устройстве"));
  [["Состояние", !source.available ? "Нет связи" : (source.powered === false ? "Выключено" : "В сети")],
    ["Комната", source.roomName || "Не назначена"],
    ["Производитель", device && device.manufacturer || "Не указан"],
    ["Модель", device && device.model || "Не указана"]].forEach(([label, value]) => {
    const row = el("div", "energy-info-row");
    row.appendChild(el("span", null, label));
    row.appendChild(el("strong", null, value));
    info.appendChild(row);
  });
  sidebar.appendChild(info);
  layout.appendChild(sidebar);
  container.appendChild(layout);
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
    ? `${selected.length} ${deviceWord(selected.length)} · выбранные источники`
    : "Источники не выбраны"));
  head.appendChild(copy);
  head.appendChild(energyPeriodButtons(panel, panel._shell.homeSections.energy, deps));
  card.appendChild(head);
  const layout = el("div", "energy-history-layout");
  layout.appendChild(renderEnergyHistoryChart(panel, { id: "selection", name: "выбранных источников" }, deps, () => {
    panel._energyHistoryError = null;
    loadEnergyHistory(panel);
  }));
  const sources = el("div", "energy-current-sources");
  sources.appendChild(el("h4", null, "Источники"));
  selected.slice(0, 2).forEach((source) => {
    const row = el("button", "energy-current-source");
    row.type = "button";
    row.addEventListener("click", () => showEnergyDevice(panel, panel._shell.homeSections.energy, source.id));
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
    const row = el("div", `energy-device-card${source.available ? "" : " is-unavailable"}`);
    const open = el("button", "energy-device-card-open");
    open.type = "button";
    setAttr(open, "aria-label", `Открыть устройство ${source.name}`);
    open.addEventListener("click", () => showEnergyDevice(panel, container, source.id));
    open.appendChild(energyDeviceVisual(panel, source, deps));
    const identity = el("span", "energy-device-card-copy");
    identity.appendChild(el("strong", null, source.name));
    identity.appendChild(el("small", null, [source.roomName || "Без комнаты", device && device.manufacturer, device && device.model].filter(Boolean).join(" · ")));
    open.appendChild(identity);
    const live = el("span", "energy-device-value energy-device-live is-accent");
    live.appendChild(el("b", null, source.available ? sourceMetric(source, "currentPowerW", "Вт") : "—"));
    live.appendChild(el("small", null, source.available
      ? `${sourceMetric(source, "currentA", "А", 2)} · ${sourceMetric(source, "voltageV", "В")}`
      : "актуальных данных нет"));
    open.appendChild(live);
    const accumulated = el("span", "energy-device-value energy-device-accumulated");
    accumulated.appendChild(el("b", null, sourceMetric(source, source.todayKwh !== null && source.todayKwh !== undefined ? "todayKwh" : "totalKwh", "кВт·ч", 2)));
    accumulated.appendChild(el("small", null, source.todayKwh !== null && source.todayKwh !== undefined ? "за сегодня" : "накоплено"));
    open.appendChild(accumulated);
    const isPoweredOff = source.available && source.powered === false;
    const statusTone = !source.available ? "is-offline" : (isPoweredOff ? "is-powered-off" : "is-online");
    const status = el("span", `energy-device-status ${statusTone}`);
    status.appendChild(el("strong", null, !source.available ? "Нет связи" : (isPoweredOff ? "Выключен" : "В сети")));
    status.appendChild(el("small", null, !source.available ? "проверьте устройство" : (isPoweredOff ? "питание отключено" : "обновляется")));
    open.appendChild(status);
    open.appendChild(el("span", "energy-overview-chevron", "›"));
    row.appendChild(open);
    const actionId = source.powered === false ? "turn_on" : "turn_off";
    const action = energyPowerAction(panel, source, actionId);
    if (action) {
      const quick = el("button", `secondary energy-device-quick${actionId === "turn_off" ? " is-danger" : ""}`,
        actionId === "turn_off" ? "Отключить" : "Включить");
      quick.type = "button";
      quick.disabled = panel._busy || !source.available;
      setAttr(quick, "aria-label", `${actionId === "turn_off" ? "Отключить" : "Включить"} ${source.name}`);
      quick.addEventListener("click", () => runEnergyPowerAction(panel, source, actionId));
      row.appendChild(quick);
    }
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
      save.disabled = panel._energySettingsSaving || (!draft.useAllDevices && !draft.selectedDeviceIds.length);
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
  save.disabled = panel._energySettingsSaving || (!draft.useAllDevices && !draft.selectedDeviceIds.length);
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
  const historyStore = (panel._energyHistoryMetric || "power") === "energy" ? panel._energyConsumptionHistory : panel._energyHistory;
  const history = historyStore && historyStore.selection;
  const peak = Array.isArray(history) && history.length
    ? `${number(Math.max(...history.map((point) => Number(point.mean) || 0)))} ${(panel._energyHistoryMetric || "power") === "energy" ? "кВт·ч" : "Вт"}` : "—";
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
    const consumption = {};
    (response && Array.isArray(response.series) ? response.series : [])
      .forEach((series) => {
        if (![["power", "W"], ["energy", "kWh"]].some(([metric, unit]) => series.metric === metric && series.unit === unit)) return;
        const source = energy.sources.find((item) => item.id === series.sourceId
          || item.deviceId === series.deviceId);
        const key = series.scope === "selection" ? "selection" : (source && source.id || series.deviceId || series.sourceId);
        const target = series.metric === "energy" ? consumption : history;
        target[key] = (series.points || []).map((point) => ({
          start: point.at,
          mean: point.value,
        }));
      });
    const aggregateSelection = (target) => {
      delete target.selection;
      const selectedIds = new Set(selectedSources(energy).map((source) => source.id));
      const values = new Map();
      Object.entries(target).forEach(([sourceId, points]) => {
        if (!selectedIds.has(sourceId)) return;
        (points || []).forEach((point) => {
          const value = Number(point.mean);
          if (!point.start || !Number.isFinite(value)) return;
          values.set(point.start, (values.get(point.start) || 0) + value);
        });
      });
      target.selection = [...values.entries()].sort(([left], [right]) => left.localeCompare(right))
        .map(([start, mean]) => ({ start, mean }));
    };
    aggregateSelection(history);
    aggregateSelection(consumption);
    panel._energyHistory = history;
    panel._energyConsumptionHistory = consumption;
    panel._energyHistoryError = null;
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
