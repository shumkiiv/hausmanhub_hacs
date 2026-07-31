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

function energyMetric(deps, label, value, tone = "") {
  const { el } = deps;
  const item = el("div", `energy-metric${tone ? ` ${tone}` : ""}`);
  item.appendChild(el("span", null, label));
  item.appendChild(el("strong", null, value));
  return item;
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
  setAttr(chart, "aria-label", `График мощности ${source.name} за последние 24 часа`);
  values.slice(-24).forEach((value) => {
    const bar = el("span", "energy-history-bar");
    bar.style.height = `${Math.max(4, (value / max) * 100)}%`;
    setAttr(bar, "title", `${number(value)} Вт`);
    chart.appendChild(bar);
  });
  wrap.appendChild(chart);
  wrap.appendChild(el("div", "energy-chart-caption", "Почасовая средняя мощность · последние 24 часа"));
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

function settingsPanel(panel, energy, deps) {
  const { el, setAttr } = deps;
  const draft = panel._energyDraft || {
    displayUnits: energy.settings.displayUnits || "watts",
    showVoltage: energy.settings.showVoltage !== false,
    aggregation: energy.settings.aggregation || "combined",
    useAllDevices: energy.settings.useAllDevices !== false,
    selectedDeviceIds: [...(energy.selectedSourceIds || [])],
  };
  panel._energyDraft = draft;
  const card = el("section", "card energy-settings-card");
  const heading = el("div", "energy-settings-head");
  const copy = el("div");
  copy.appendChild(el("h3", null, "Настройка карточки"));
  copy.appendChild(el("p", "section-intro", "Выберите единицы, способ показа и физические устройства. Настройка хранится в Home Assistant для всех клиентов."));
  heading.appendChild(copy);
  const close = el("button", "secondary", "Закрыть");
  close.type = "button";
  close.addEventListener("click", () => { panel._energySettingsOpen = false; panel._render(); });
  heading.appendChild(close);
  card.appendChild(heading);
  const fieldGrid = el("div", "energy-settings-grid");
  const unitField = el("fieldset", "energy-choice-field");
  unitField.appendChild(el("legend", null, "Основное значение"));
  const units = el("div", "energy-segments");
  [["watts", "Вт"], ["amps", "Амперы"], ["both", "Оба"]].forEach(([value, label]) => {
    const button = el("button", draft.displayUnits === value ? "is-selected" : "", label);
    button.type = "button";
    setAttr(button, "aria-pressed", draft.displayUnits === value ? "true" : "false");
    button.addEventListener("click", () => { draft.displayUnits = value; panel._render(); });
    units.appendChild(button);
  });
  unitField.appendChild(units);
  fieldGrid.appendChild(unitField);
  const aggregationField = el("fieldset", "energy-choice-field");
  aggregationField.appendChild(el("legend", null, "Отображение источников"));
  const aggregation = el("div", "energy-segments");
  [["combined", "Вместе"], ["separate", "Раздельно"]].forEach(([value, label]) => {
    const button = el("button", draft.aggregation === value ? "is-selected" : "", label);
    button.type = "button";
    setAttr(button, "aria-pressed", draft.aggregation === value ? "true" : "false");
    button.addEventListener("click", () => { draft.aggregation = value; panel._render(); });
    aggregation.appendChild(button);
  });
  aggregationField.appendChild(aggregation);
  aggregationField.appendChild(el("small", "energy-choice-help", "«Вместе» показывает сумму мощности и тока. Складывать ток корректно только для источников одной линии; общий счётчик и его дочерние розетки одновременно выбирать не следует."));
  fieldGrid.appendChild(aggregationField);
  const voltage = el("label", "energy-toggle-row");
  const voltageInput = el("input");
  voltageInput.type = "checkbox";
  voltageInput.checked = draft.showVoltage;
  voltageInput.addEventListener("change", () => { draft.showVoltage = voltageInput.checked; });
  voltage.appendChild(voltageInput);
  const voltageCopy = el("span");
  voltageCopy.appendChild(el("strong", null, "Показывать напряжение"));
  voltageCopy.appendChild(el("small", null, "Напряжение не суммируется; для нескольких источников показывается среднее доступное значение."));
  voltage.appendChild(voltageCopy);
  fieldGrid.appendChild(voltage);
  card.appendChild(fieldGrid);
  const all = el("label", "energy-toggle-row energy-all-toggle");
  const allInput = el("input");
  allInput.type = "checkbox";
  allInput.checked = draft.useAllDevices;
  allInput.addEventListener("change", () => { draft.useAllDevices = allInput.checked; panel._render(); });
  all.appendChild(allInput);
  const allCopy = el("span");
  allCopy.appendChild(el("strong", null, "Все подходящие устройства"));
  allCopy.appendChild(el("small", null, "Новые физические устройства с измерениями энергии будут добавляться автоматически."));
  all.appendChild(allCopy);
  card.appendChild(all);
  const list = el("div", `energy-source-picker${draft.useAllDevices ? " is-disabled" : ""}`);
  energy.sources.forEach((source) => {
    const option = el("label", "energy-source-option");
    const input = el("input");
    input.type = "checkbox";
    input.disabled = draft.useAllDevices;
    input.checked = draft.useAllDevices || draft.selectedDeviceIds.includes(source.id);
    input.addEventListener("change", () => {
      const selected = new Set(draft.selectedDeviceIds);
      if (input.checked) selected.add(source.id); else selected.delete(source.id);
      draft.selectedDeviceIds = [...selected];
    });
    option.appendChild(input);
    const optionCopy = el("span");
    optionCopy.appendChild(el("strong", null, source.name));
    optionCopy.appendChild(el("small", null, `${source.roomName || "Без комнаты"} · ${sourceMetric(source, "currentPowerW", "Вт")}`));
    option.appendChild(optionCopy);
    list.appendChild(option);
  });
  card.appendChild(list);
  const actions = el("div", "energy-settings-actions");
  const save = el("button", null, "Сохранить настройку");
  save.type = "button";
  save.disabled = panel._busy || (!draft.useAllDevices && !draft.selectedDeviceIds.length);
  save.addEventListener("click", () => panel._saveEnergySettings());
  actions.appendChild(save);
  actions.appendChild(el("span", "muted", save.disabled && !panel._busy ? "Выберите хотя бы одно устройство." : "Изменения применятся к карточке и разделу энергии."));
  card.appendChild(actions);
  return card;
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
  const icon = el("span", "home-section-icon");
  icon.appendChild(svgIcon("energy"));
  heading.appendChild(icon);
  const copy = el("div");
  copy.appendChild(el("h2", null, "Энергия"));
  copy.appendChild(el("p", "section-intro", "Текущая нагрузка, напряжение, статистика и управление устройствами"));
  heading.appendChild(copy);
  container.appendChild(heading);
  if (panel._energySettingsOpen) container.appendChild(settingsPanel(panel, energy, deps));
  const pageLayout = el("div", "energy-page-layout");
  const mainColumn = el("div", "energy-main-column");
  const hero = el("section", "card energy-hero");
  const main = el("div", "energy-hero-primary");
  main.appendChild(el("span", "assistant-field-label", "Потребление сейчас"));
  main.appendChild(el("strong", null, energy.available ? primaryEnergyValue(energy) : "Нет данных"));
  main.appendChild(el("small", null, energy.settings.aggregation === "separate"
    ? "Устройства показаны раздельно" : "Сумма выбранных источников"));
  hero.appendChild(main);
  const metrics = el("div", "energy-metric-grid");
  if (energy.settings.displayUnits !== "amps") metrics.appendChild(energyMetric(deps, "Мощность", sourceMetric(energy, "currentPowerW", "Вт"), "is-accent"));
  if (energy.settings.displayUnits !== "watts") metrics.appendChild(energyMetric(deps, "Ток", sourceMetric(energy, "currentA", "А", 2), "is-accent"));
  if (energy.settings.showVoltage) metrics.appendChild(energyMetric(deps, "Напряжение", sourceMetric(energy, "voltageV", "В")));
  hero.appendChild(metrics);
  mainColumn.appendChild(hero);
  const selected = selectedSources(energy);
  const allSources = energy.sources;
  const historyCard = el("section", "card energy-load-card energy-selection-history");
  const historyHead = el("div", "energy-card-head");
  historyHead.appendChild(el("h3", null, "Потребление за 24 часа"));
  historyHead.appendChild(el("span", "status-badge", "Home Assistant"));
  historyCard.appendChild(historyHead);
  historyCard.appendChild(historyBars(panel, { id: "selection", name: "выбранных источников" }, deps));
  mainColumn.appendChild(historyCard);
  const chartCard = el("section", "card energy-load-card");
  const chartHead = el("div", "energy-card-head");
  chartHead.appendChild(el("h3", null, "Распределение нагрузки"));
  chartHead.appendChild(el("span", "status-badge", "Сейчас"));
  chartCard.appendChild(chartHead);
  const maxPower = Math.max(...selected.map((item) => Number(item.currentPowerW) || 0), 1);
  const bars = el("div", "energy-load-bars");
  selected.forEach((item) => {
    const row = el("button", "energy-load-row");
    row.type = "button";
    setAttr(row, "aria-label", `Открыть устройство ${item.name}`);
    row.addEventListener("click", () => { panel._energySelectedDeviceId = item.id; panel._render(); });
    row.appendChild(el("span", "energy-load-name", item.name));
    const track = el("span", "energy-load-track");
    const fill = el("span", "energy-load-fill");
    fill.style.width = `${Math.max(2, ((Number(item.currentPowerW) || 0) / maxPower) * 100)}%`;
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el("strong", null, sourceMetric(item, "currentPowerW", "Вт")));
    bars.appendChild(row);
  });
  chartCard.appendChild(bars);
  mainColumn.appendChild(chartCard);
  const devicesHead = el("div", "energy-card-head energy-devices-head");
  devicesHead.appendChild(el("h3", null, "Устройства"));
  devicesHead.appendChild(el("span", "muted", `${allSources.length} доступно · ${selected.length} на главной`));
  mainColumn.appendChild(devicesHead);
  const grid = el("div", "energy-device-grid");
  allSources.forEach((item) => {
    const card = el("button", `energy-device-card${item.available ? "" : " is-unavailable"}`);
    card.type = "button";
    card.addEventListener("click", () => { panel._energySelectedDeviceId = item.id; panel._render(); });
    const cardIcon = el("span", "energy-device-card-icon");
    cardIcon.appendChild(svgIcon("energy"));
    card.appendChild(cardIcon);
    const cardCopy = el("span", "energy-device-card-copy");
    cardCopy.appendChild(el("strong", null, item.name));
    cardCopy.appendChild(el("small", null, item.roomName || "Без комнаты"));
    const values = el("span", "energy-device-values");
    values.appendChild(el("b", null, sourceMetric(item, "currentPowerW", "Вт")));
    values.appendChild(el("span", null, `${sourceMetric(item, "currentA", "А", 2)} · ${sourceMetric(item, "voltageV", "В")}`));
    cardCopy.appendChild(values);
    card.appendChild(cardCopy);
    card.appendChild(el("span", "energy-overview-chevron", "›"));
    grid.appendChild(card);
  });
  mainColumn.appendChild(grid);
  pageLayout.appendChild(mainColumn);

  const sidebar = el("aside", "energy-sidebar");
  const summary = el("section", "card energy-sidebar-summary");
  summary.appendChild(el("h3", null, "Сводка"));
  const availableCount = allSources.filter((item) => item.available).length;
  [
    ["Источники", String(allSources.length), ""],
    ["Доступны", String(availableCount), "is-success"],
    ["Накоплено", sourceMetric(energy, "totalKwh", "кВт·ч", 3), ""],
  ].forEach(([label, value, tone]) => {
    const row = el("div", `energy-sidebar-row${tone ? ` ${tone}` : ""}`);
    row.appendChild(el("span", null, label));
    row.appendChild(el("strong", null, value));
    summary.appendChild(row);
  });
  sidebar.appendChild(summary);

  const truth = el("section", "card energy-truth-card");
  const truthIcon = el("span", "energy-truth-icon");
  truthIcon.appendChild(svgIcon("energy"));
  truth.appendChild(truthIcon);
  truth.appendChild(el("h3", null, "Единый источник истины"));
  truth.appendChild(el("p", null, "Выбор устройств и единиц хранится в Home Assistant и одинаков для планшета и панели HausmanHub."));
  sidebar.appendChild(truth);

  const configure = el("button", "secondary energy-sidebar-configure", "Настроить карточку");
  configure.type = "button";
  configure.addEventListener("click", () => {
    panel._energySettingsOpen = !panel._energySettingsOpen;
    panel._render();
  });
  const configureIcon = el("span", "energy-sidebar-configure-icon");
  configureIcon.appendChild(svgIcon("settings"));
  configure.appendChild(configureIcon);
  sidebar.appendChild(configure);
  pageLayout.appendChild(sidebar);
  container.appendChild(pageLayout);
}

export async function loadEnergyHistory(panel) {
  if (!panel._hass || panel._energyHistoryLoading) return;
  const energy = panel._homeDashboard && panel._homeDashboard.energy;
  if (!energy || !Array.isArray(energy.sources)) return;
  if (typeof panel._hass.callApi !== "function") return;
  panel._energyHistoryLoading = true;
  try {
    const end = new Date();
    const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
    const params = new URLSearchParams({
      from: start.toISOString(),
      to: end.toISOString(),
      interval: "1h",
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
  } finally {
    panel._energyHistoryLoading = false;
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
