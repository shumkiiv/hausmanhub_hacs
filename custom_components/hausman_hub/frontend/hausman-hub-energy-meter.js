/* Energy meter card: readings, submission schedule, corrections and history. */

export const meterNumber = (value, digits = 1) => Number.isFinite(Number(value))
  ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(Number(value))
  : "—";

export function daysWord(count) {
  const value = Math.abs(Number(count)) % 100;
  const tail = value % 10;
  if (value > 10 && value < 20) return "дней";
  if (tail === 1) return "день";
  if (tail > 1 && tail < 5) return "дня";
  return "дней";
}

export function formatMeterDate(value) {
  if (!value) return "—";
  const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00` : value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

function formatMeterDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function meterStatusMeta(meter) {
  const status = meter && meter.submission && meter.submission.status || "none";
  const table = {
    disabled: { label: "Напоминания отключены", tone: "" },
    none: { label: "Дата передачи не настроена", tone: "" },
    upcoming: { label: "Передача показаний скоро", tone: "is-warning" },
    due: { label: "Пора передать показания", tone: "is-warning" },
    overdue: { label: "Передача показаний просрочена", tone: "is-danger" },
  };
  return table[status] || table.none;
}

export function meterConfigured(meter) {
  return !!(meter && meter.settings && meter.settings.enabled
    && meter.reading && meter.reading.currentKwh !== null && meter.reading.currentKwh !== undefined);
}

export function meterReminderText(meter) {
  if (!meterConfigured(meter)) return "";
  const status = meter.submission && meter.submission.status;
  if (status === "due") return "Пора передать показания счётчика";
  if (status === "overdue") return "Передача показаний просрочена";
  if (status === "upcoming") return `Передача показаний ${formatMeterDate(meter.submission.nextDate)}`;
  return "";
}

function meterDraft(panel, meter) {
  if (!panel._energyMeterDraft) {
    panel._energyMeterDraft = {
      enabled: meter && meter.settings ? meter.settings.enabled !== false : true,
      submissionDayOfMonth: meter && meter.settings && meter.settings.submissionDayOfMonth || 25,
      reminderDaysBefore: meter && meter.settings ? Number(meter.settings.reminderDaysBefore) || 0 : 3,
      submit: "",
      correct: "",
    };
  }
  return panel._energyMeterDraft;
}

function meterNumberInput(deps, value, ariaLabel, onInput) {
  const { el, setAttr } = deps;
  const input = el("input", "energy-meter-input");
  input.type = "number";
  input.step = "any";
  input.value = value;
  setAttr(input, "aria-label", ariaLabel);
  input.addEventListener("input", () => onInput(input.value));
  return input;
}

export function renderEnergyMeterCard(panel, deps) {
  const { el } = deps;
  const meter = panel._energyMeter;
  const status = meterStatusMeta(meter);
  const card = el("section", "card energy-meter-card");
  const head = el("div", "energy-card-head energy-meter-head");
  const title = el("div", "energy-card-title");
  title.appendChild(el("h3", null, "Показания счётчика"));
  title.appendChild(el("small", null, "Передача показаний и месячный расчётный цикл"));
  head.appendChild(title);
  head.appendChild(el("span", `energy-meter-status${status.tone ? ` ${status.tone}` : ""}`, status.label));
  card.appendChild(head);

  if (panel._energyMeterError) {
    const error = el("div", "energy-meter-error");
    error.appendChild(el("span", null, "Не удалось получить показания счётчика."));
    const retry = el("button", "secondary", "Повторить");
    retry.type = "button";
    retry.disabled = panel._energyMeterLoading;
    retry.addEventListener("click", () => loadEnergyMeter(panel));
    error.appendChild(retry);
    card.appendChild(error);
    return card;
  }
  if (!meter) {
    card.appendChild(el("p", "energy-meter-loading", panel._energyMeterLoading
      ? "Показания счётчика загружаются…" : "Данные счётчика появятся после следующего обновления."));
    return card;
  }

  if (meter.source && meter.source.state === "reset_detected") {
    card.appendChild(el("p", "energy-meter-warning",
      "Накопительный счётчик сброшен или состав источников изменился. Расход цикла недоступен до новой передачи или осознанной корректировки."));
  }

  const value = el("div", "energy-meter-value");
  value.appendChild(el("strong", null, meter.reading && meter.reading.currentKwh !== null && meter.reading.currentKwh !== undefined
    ? `${meterNumber(meter.reading.currentKwh)} кВт·ч` : "—"));
  if (meter.reading && meter.reading.estimated) value.appendChild(el("span", "energy-meter-badge", "расчётное"));
  card.appendChild(value);

  const rows = el("div", "energy-meter-rows");
  [["Расход с последней передачи", meter.cycle && meter.cycle.consumptionKwh !== null && meter.cycle.consumptionKwh !== undefined
    ? `${meterNumber(meter.cycle.consumptionKwh)} кВт·ч` : "—"],
    ["Последняя передача", formatMeterDate(meter.submission && meter.submission.lastDate)],
    ["Следующая передача", meter.submission && meter.submission.nextDate
      ? `${formatMeterDate(meter.submission.nextDate)}${Number.isFinite(Number(meter.submission.daysUntil)) ? ` · через ${meter.submission.daysUntil} ${daysWord(meter.submission.daysUntil)}` : ""}`
      : "—"],
  ].forEach(([label, text]) => {
    const row = el("div", "energy-meter-row");
    row.appendChild(el("span", null, label));
    row.appendChild(el("strong", null, text));
    rows.appendChild(row);
  });
  card.appendChild(rows);

  if (panel._energyMeterNotice) card.appendChild(el("p", "energy-meter-notice", panel._energyMeterNotice));

  const draft = meterDraft(panel, meter);
  const configure = el("div", "energy-meter-form");
  configure.appendChild(el("span", "energy-settings-label", "Расписание передачи"));
  const configureGrid = el("div", "energy-meter-form-grid");
  const dayField = el("label", "energy-meter-field");
  dayField.appendChild(el("span", null, "День месяца"));
  dayField.appendChild(meterNumberInput(deps, draft.submissionDayOfMonth, "День месяца передачи показаний", (value) => {
    draft.submissionDayOfMonth = Math.max(1, Math.min(31, Number(value) || 1));
  }));
  configureGrid.appendChild(dayField);
  const reminderField = el("label", "energy-meter-field");
  reminderField.appendChild(el("span", null, "Напомнить заранее, дней"));
  reminderField.appendChild(meterNumberInput(deps, draft.reminderDaysBefore, "Дней до напоминания о передаче", (value) => {
    draft.reminderDaysBefore = Math.max(0, Math.min(30, Number(value) || 0));
  }));
  configureGrid.appendChild(reminderField);
  const enabledField = el("label", "energy-meter-field energy-meter-enabled");
  const enabledBox = el("input");
  enabledBox.type = "checkbox";
  enabledBox.checked = draft.enabled;
  enabledBox.addEventListener("change", () => { draft.enabled = enabledBox.checked; });
  enabledField.appendChild(enabledBox);
  enabledField.appendChild(el("span", null, "Напоминания включены"));
  configureGrid.appendChild(enabledField);
  configure.appendChild(configureGrid);
  const configureSave = el("button", "secondary", "Сохранить расписание");
  configureSave.type = "button";
  configureSave.disabled = panel._energyMeterSaving;
  configureSave.addEventListener("click", () => postEnergyMeterAction(panel, {
    action: "configure",
    settings: {
      enabled: draft.enabled !== false,
      submissionDayOfMonth: draft.submissionDayOfMonth,
      reminderDaysBefore: draft.reminderDaysBefore,
    },
  }, "Расписание передачи сохранено."));
  configure.appendChild(configureSave);
  card.appendChild(configure);

  const actions = el("div", "energy-meter-actions");
  const submitBox = el("div", "energy-meter-action");
  submitBox.appendChild(el("span", "energy-settings-label", "Передать показания"));
  const submitButton = el("button", "energy-meter-submit", "Передать показания");
  submitButton.type = "button";
  submitButton.disabled = panel._energyMeterSaving || !String(draft.submit).trim();
  submitButton.addEventListener("click", () => postEnergyMeterAction(panel, {
    action: "submit",
    readingKwh: Number(String(draft.submit).replace(",", ".")),
  }, "Показания переданы. Начался новый месячный цикл."));
  submitBox.appendChild(meterNumberInput(deps, draft.submit, "Новое показание счётчика, кВт·ч", (value) => {
    draft.submit = value;
    submitButton.disabled = panel._energyMeterSaving || !String(value).trim();
  }));
  submitBox.appendChild(submitButton);
  actions.appendChild(submitBox);
  const correctBox = el("div", "energy-meter-action");
  correctBox.appendChild(el("span", "energy-settings-label", "Скорректировать текущее значение"));
  const correctButton = el("button", "secondary", "Скорректировать");
  correctButton.type = "button";
  correctButton.disabled = panel._energyMeterSaving || !String(draft.correct).trim();
  correctButton.addEventListener("click", () => postEnergyMeterAction(panel, {
    action: "correct",
    readingKwh: Number(String(draft.correct).replace(",", ".")),
  }, "Текущее показание скорректировано."));
  correctBox.appendChild(meterNumberInput(deps, draft.correct, "Скорректированное показание, кВт·ч", (value) => {
    draft.correct = value;
    correctButton.disabled = panel._energyMeterSaving || !String(value).trim();
  }));
  correctBox.appendChild(correctButton);
  actions.appendChild(correctBox);
  card.appendChild(actions);

  const history = Array.isArray(meter.history) ? meter.history : [];
  if (history.length) {
    const list = el("div", "energy-meter-history");
    list.appendChild(el("span", "energy-settings-label", "История передач и корректировок"));
    history.slice(0, 10).forEach((entry) => {
      const row = el("div", "energy-meter-history-row");
      row.appendChild(el("span", `energy-meter-history-kind ${entry.kind === "correction" ? "is-correction" : "is-submission"}`,
        entry.kind === "correction" ? "Корректировка" : "Передача"));
      row.appendChild(el("strong", null, `${meterNumber(entry.readingKwh)} кВт·ч`));
      row.appendChild(el("small", null, formatMeterDateTime(entry.recordedAt)));
      list.appendChild(row);
    });
    card.appendChild(list);
  }
  return card;
}

export async function loadEnergyMeter(panel) {
  if (!panel._hass || panel._energyMeterLoading || typeof panel._hass.callApi !== "function") return;
  panel._energyMeterLoading = true;
  try {
    panel._energyMeter = await panel._hass.callApi("GET", "hausman_hub/v1/energy/meter");
    panel._energyMeterError = null;
  } catch (error) {
    panel._energyMeterError = error && error.message || "meter_unavailable";
  } finally {
    panel._energyMeterLoading = false;
    if (panel._activeSection === "energy" || panel._activeSection === "overview") panel._render();
  }
}

export async function postEnergyMeterAction(panel, payload, successNotice) {
  if (panel._energyMeterSaving || !panel._hass || typeof panel._hass.callApi !== "function") return;
  const readingKwh = payload && payload.readingKwh;
  if (readingKwh !== undefined && !Number.isFinite(readingKwh)) {
    panel._energyMeterNotice = "Введите показание числом, например 18342.4.";
    panel._render();
    return;
  }
  panel._energyMeterSaving = true;
  panel._energyMeterNotice = "";
  try {
    const response = await panel._hass.callApi("POST", "hausman_hub/v1/energy/meter", {
      expectedRevision: panel._energyMeter && Number.isFinite(Number(panel._energyMeter.revision))
        ? Number(panel._energyMeter.revision) : 0,
      ...payload,
    });
    panel._energyMeter = response || panel._energyMeter;
    panel._energyMeterDraft = null;
    panel._energyMeterError = null;
    panel._energyMeterNotice = successNotice || "Показания сохранены.";
  } catch (error) {
    if (error && error.status === 409) {
      panel._energyMeterNotice = "Показания изменились в другом окне. Данные обновлены, проверьте ввод и повторите.";
    } else {
      panel._energyMeterNotice = "Не удалось сохранить показания. Попробуйте ещё раз.";
    }
  } finally {
    panel._energyMeterSaving = false;
    await loadEnergyMeter(panel);
    panel._render();
  }
}
