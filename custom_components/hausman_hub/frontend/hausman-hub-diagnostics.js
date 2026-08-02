/* Redacted owner-facing diagnostics shared by the system page and copied report. */

export function buildDiagnosticChecks(panel, readinessLabels) {
  const setup = panel._settings.setup || {};
  const summary = setup.summary || {};
  const readiness = panel._data && panel._data.readiness || {};
  const energy = panel._homeDashboard && panel._homeDashboard.energy;
  const scenarios = panel._scenarios.list && Array.isArray(panel._scenarios.list.scenarios)
    ? panel._scenarios.list.scenarios : null;
  const roomCount = Number(summary.room_count) || 0;
  const deviceCount = Number(summary.device_count) || 0;
  const climateDisabled = readiness.status === "disabled" || readiness.bridge_mode === "disabled";
  const climateReady = readiness.status === "ready";
  const setupReady = setup.status === "ready" || setup.status === "attention";
  const scenarioEnabled = scenarios ? scenarios.filter((item) => item.enabled !== false).length : 0;
  const energySources = energy && Array.isArray(energy.sources) ? energy.sources : [];
  const energyAvailable = energySources.filter((source) => source.available === true).length;
  return [
    {
      icon: "device", title: "Связь с Home Assistant",
      value: panel._error || !panel._data ? "Нет ответа" : "Соединение работает",
      detail: panel._error || !panel._data
        ? "Панель не получила обязательную системную сводку."
        : "Панель получила системную сводку и может обновлять данные.",
      tone: panel._error || !panel._data ? "is-error" : "is-ready",
      hint: panel._error || !panel._data ? "Проверьте Home Assistant и повторите обновление." : "",
    },
    {
      icon: "rooms", title: "Сохранённая конфигурация",
      value: setupReady
        ? `${roomCount} ${panel._roomCountWord(roomCount)} · ${deviceCount} ${panel._deviceCountWord(deviceCount)}`
        : "Настройка не завершена",
      detail: setupReady
        ? "Комнаты, устройства и цели сохранены в Home Assistant."
        : "Климатический контур ещё не получил полную конфигурацию.",
      tone: setupReady ? "is-ready" : "is-warning",
      hint: setupReady ? "" : "Откройте «Комнаты» и завершите мастер настройки.",
    },
    {
      icon: "thermometer", title: "Климатический контур",
      value: climateDisabled ? "Выключен в настройках" : (readinessLabels[readiness.status] || "Состояние неизвестно"),
      detail: climateDisabled
        ? "Конфигурация сохранена, но наблюдение и автоматические команды сейчас отключены."
        : (climateReady
          ? "Контур получает актуальные данные и прошёл проверки готовности."
          : "Контур не прошёл одну или несколько проверок готовности."),
      tone: climateDisabled ? "is-neutral" : (climateReady ? "is-ready" : "is-warning"),
      hint: climateDisabled || climateReady ? "" : "Откройте раздел «Климат» и проверьте указанные причины.",
    },
    {
      icon: "play", title: "Сценарии",
      value: panel._scenarios.loading
        ? "Проверяются…"
        : (scenarios ? `${scenarios.length} сохранено · ${scenarioEnabled} включено` : "Список недоступен"),
      detail: scenarios
        ? "Сценарии читаются из единого хранилища HausmanHub."
        : "Панель пока не получила список сохранённых сценариев.",
      tone: scenarios ? "is-ready" : (panel._scenarios.loading ? "is-neutral" : "is-warning"),
      hint: scenarios || panel._scenarios.loading ? "" : "Обновите состояние. Если ошибка повторится, проверьте журнал Home Assistant.",
    },
    {
      icon: "energy", title: "Энергия",
      value: energy && energy.available
        ? `${energyAvailable} из ${energySources.length} источников доступны`
        : "Данные не поступают",
      detail: energy && energy.available
        ? "Потребление и доступность источников получены с главного экрана."
        : "Нет подтверждённых показаний выбранных источников энергии.",
      tone: energy && energy.available ? "is-ready" : "is-warning",
      hint: energy && energy.available ? "" : "Откройте «Энергия» и проверьте выбранные источники.",
    },
  ];
}

export function diagnosticSummaryText(panel, checks) {
  return [
    "HausmanHub — техническая сводка",
    `Версия: ${panel._data && panel._data.integration_version || "не определена"}`,
    "Подключение: HausmanHub в Home Assistant",
    "",
    "Проверки:",
    ...checks.map((check) => `${check.title}: ${check.value}`),
  ].join("\n");
}

export function renderDiagnosticDetails(panel, health, checks, deps) {
  const { el, svgIcon } = deps;
  const diagnosticGrid = el("div", "system-diagnostic-grid");
  checks.forEach((check) => {
    const card = el("article", `system-diagnostic-item ${check.tone}`);
    const icon = el("span", "system-diagnostic-icon");
    icon.appendChild(svgIcon(check.icon));
    card.appendChild(icon);
    const body = el("div", "system-diagnostic-copy");
    body.appendChild(el("strong", null, check.title));
    body.appendChild(el("b", null, check.value));
    body.appendChild(el("p", "muted", check.detail));
    card.appendChild(body);
    diagnosticGrid.appendChild(card);
  });
  health.appendChild(diagnosticGrid);
  const hints = checks.filter((check) => check.hint);
  if (hints.length) {
    const attention = el("div", "system-diagnostic-attention");
    attention.appendChild(el("strong", null, "Что проверить"));
    const list = el("ul");
    hints.forEach((check) => list.appendChild(el("li", null, check.hint)));
    attention.appendChild(list);
    health.appendChild(attention);
  }
  const report = el("details", "system-technical-report");
  report.appendChild(el("summary", null, "Показать обезличенную техническую сводку"));
  report.appendChild(el("pre", null, diagnosticSummaryText(panel, checks)));
  health.appendChild(report);
}
