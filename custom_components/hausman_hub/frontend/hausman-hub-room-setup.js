export function renderFirstRunRoom(card, deps) {
const {
  el, setAttr, numberField, selectField, normalizedText,
  ACTIVE_DEVICE_TYPES, STRATEGY_ORDER, ROOM_SETUP_PANES,
} = deps;
  const room = (this._firstRun.options.rooms || []).find((item) => item.id === this._firstRun.roomId);
  if (!room) {
    this._firstRunBackToRooms();
    return;
  }
  const state = this._firstRunRoomState(room);
  const roomHeading = el("div", "room-setup-heading");
  const roomHeadingCopy = el("div");
  roomHeadingCopy.appendChild(el("div", "room-setup-eyebrow", "Настройка комнаты"));
  roomHeadingCopy.appendChild(el("h2", null, room.name || room.id));
  roomHeadingCopy.appendChild(el("div", "section-intro", "Настройте комнату по коротким шагам. Выбор сохраняется при переходе между ними."));
  roomHeading.appendChild(roomHeadingCopy);
  const roomProgress = el("span", "status-badge room-setup-progress");
  const activePaneIndex = Math.max(0, ROOM_SETUP_PANES.findIndex((pane) => pane.id === this._activeRoomSetupPane));
  roomProgress.textContent = `${activePaneIndex + 1} из ${ROOM_SETUP_PANES.length}`;
  roomHeading.appendChild(roomProgress);
  card.appendChild(roomHeading);
  const roomNav = el("nav", "room-setup-nav");
  setAttr(roomNav, "aria-label", "Этапы настройки комнаты");
  ROOM_SETUP_PANES.forEach((pane, index) => {
    const button = el("button", `room-setup-step${pane.id === this._activeRoomSetupPane ? " is-current" : ""}`);
    button.type = "button";
    setAttr(button, "aria-current", pane.id === this._activeRoomSetupPane ? "step" : "false");
    button.appendChild(el("span", "room-setup-step-number", String(index + 1)));
    const copy = el("span", "room-setup-step-copy");
    copy.appendChild(el("strong", null, pane.label));
    copy.appendChild(el("small", null, pane.description));
    button.appendChild(copy);
    button.addEventListener("click", () => {
      this._activeRoomSetupPane = pane.id;
      this._render();
    });
    roomNav.appendChild(button);
  });
  card.appendChild(roomNav);
  const fields = {
    climateSources: {}, devices: [], maxTemperature: null, minTemperature: null, room: null,
  };
  const scheduleSection = el("section", "wizard-section");
  scheduleSection.appendChild(el("h3", null, "Расписание комнаты"));
  scheduleSection.appendChild(el("div", "settings-explainer", "Hausman Hub будет сам переключать дневные и ночные цели в указанное время. Если автоматический режим выключен, профиль выбирается вручную."));
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
  if (this._activeRoomSetupPane === "schedule") card.appendChild(scheduleSection);

  const profilesSection = el("section", "wizard-section");
  profilesSection.appendChild(el("h3", null, "Дневной и ночной профиль"));
  profilesSection.appendChild(el("div", "settings-explainer", "Вы задаёте желаемый комфорт, а Hausman Hub выбирает подходящее устройство. Дневные и ночные цели сохраняются отдельно."));
  profilesSection.appendChild(el("div", "muted", "Температура: 18–28 °C с шагом 0,5. Влажность: 30–70 % с шагом 1."));
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
  if (this._activeRoomSetupPane === "comfort") card.appendChild(profilesSection);

  const limitsSection = el("section", "wizard-section");
  limitsSection.appendChild(el("h3", null, "Допустимая температура комнаты"));
  limitsSection.appendChild(el("div", "settings-explainer", "Эти границы не являются целевой температурой. Они только не дают автоматике охладить или нагреть комнату сильнее допустимого."));
  limitsSection.appendChild(el("div", "muted", "Шаг необязательный. Пустое поле означает, что дополнительного ограничения нет."));
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
  if (this._activeRoomSetupPane === "limits") card.appendChild(limitsSection);

  const devicesSection = el("section", "wizard-section");
  devicesSection.appendChild(el("h3", null, "Настройка климата комнаты"));
  devicesSection.appendChild(el("div", "settings-explainer", "Этот этап разделён на две независимые задачи: сначала выберите показания, которым доверяет контур, затем — устройства, которыми он может управлять."));
  const roomlessCandidates = this._firstRunRoomlessCandidates();
  if (roomlessCandidates.length) {
    devicesSection.appendChild(el(
      "div",
      "wizard-warning",
      `Устройств без комнаты: ${roomlessCandidates.length}. Они не участвуют в климате, пока им не назначена комната в Home Assistant.`
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
  showAllLabel.appendChild(el("span", null, "Показать устройства из других комнат"));
  const roomChoices = this._firstRunRoomChoices(state, this._firstRunRoomCandidates(room.id));
  const roomlessChoices = this._firstRunRoomChoices(state, roomlessCandidates);
  const nearbyChoices = this._firstRunRoomChoices(state, this._firstRunPossibleRoomCandidates(room));
  const catalogChoices = this._firstRunRoomChoices(
    state,
    (this._firstRun.options && this._firstRun.options.devices) || []
  );
  const choices = roomChoices.concat(roomlessChoices, nearbyChoices);
  const isClimateSource = (choice) => (
    choice.type === "temperature_sensor" || choice.type === "humidity_sensor"
  );
  const roomControlChoices = roomChoices.filter((choice) => !isClimateSource(choice));
  const roomlessControlChoices = roomlessChoices.filter((choice) => !isClimateSource(choice));
  const nearbyControlChoices = nearbyChoices.filter((choice) => !isClimateSource(choice));
  const catalogControlChoices = catalogChoices.filter((choice) => !isClimateSource(choice));
  const controlChoices = choices.filter((choice) => !isClimateSource(choice));
  const irRemotes = (this._firstRun.options.ir_remotes || []).filter((remote) => remote.room_id === room.id);
  const hasClimateFacade = choices.some((choice) => (
    choice.type === "air_conditioner" || choice.type === "humidifier"
  ));
  if (irRemotes.length && !hasClimateFacade) {
    const names = irRemotes.map((remote) => `«${remote.name}»`).join(", ");
    devicesSection.appendChild(el("div", "wizard-hint", `Для ${names} нет обёртки. Создайте SmartIR climate, назначьте зоне и обновите список.`));
  }
  devicesSection.appendChild(this._firstRunClimateSources(room, fields, roomChoices));
  const controlHeading = el("div", "control-device-stage-heading");
  const controlHeadingTitle = el("div");
  controlHeadingTitle.appendChild(el("span", "setup-stage-number", "2"));
  controlHeadingTitle.appendChild(el("strong", null, "Устройства управления"));
  controlHeading.appendChild(controlHeadingTitle);
  controlHeading.appendChild(el("p", null, "Выберите, чем контур может охлаждать, обогревать или увлажнять комнату. Датчики здесь больше не показываются."));
  devicesSection.appendChild(controlHeading);
  devicesSection.appendChild(el("div", "muted control-channel-explainer", "Канал управления определяет, как отправляется команда: напрямую, через ИК-пульт или через Яндекс."));
  devicesSection.appendChild(showAllLabel);
  const search = el("input", "entity-search");
  search.type = "search";
  search.placeholder = "Найти управляющее устройство";
  setAttr(search, "aria-label", "Поиск управляющих устройств комнаты");
  devicesSection.appendChild(search);
  const searchable = [];
  if (state.showAllDevices) {
    const byRoom = new Map();
    catalogControlChoices.forEach((choice) => {
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
          groupedChoices, room, fields, catalogControlChoices, searchable
        ));
      });
    if (!catalogControlChoices.length) {
      devicesSection.appendChild(el("div", "muted", "Home Assistant пока не передал ни одного управляющего устройства."));
    }
  } else {
    const groups = this._firstRunDeviceGroups(roomControlChoices, room, fields, controlChoices, searchable);
    if (!roomControlChoices.length) groups.appendChild(el("div", "muted", "В этой комнате пока нет устройств управления климатом."));
    devicesSection.appendChild(groups);
    if (roomlessControlChoices.length) {
      devicesSection.appendChild(this._collapsibleDeviceSection(
        "Устройства без комнаты",
        "Сначала вернитесь к привязке комнат и сохраните область устройства в Home Assistant. После обновления его можно будет добавить в климатический контур.",
        this._firstRunDeviceGroups(roomlessControlChoices, room, fields, controlChoices, searchable),
        false
      ));
    }
    if (nearbyControlChoices.length) {
      devicesSection.appendChild(this._collapsibleDeviceSection(
        "Возможно, относится к этой комнате",
        "Эти климатические устройства уже привязаны к другой области Home Assistant и показаны только для проверки.",
        this._firstRunDeviceGroups(nearbyControlChoices, room, fields, controlChoices, searchable),
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
  if (this._activeRoomSetupPane === "devices") card.appendChild(devicesSection);

  const report = state.report;
  const roomReady = report && report.status === "ready" && report.save_allowed === true;
  const reviewSection = el("section", "wizard-section room-review-section");
  reviewSection.appendChild(el("h3", null, "Проверка комнаты"));
  reviewSection.appendChild(el("div", "settings-explainer", "Проверка не включает устройства. Она убеждается, что выбранные датчики, способы управления и границы не противоречат друг другу."));
  const reviewGrid = el("div", "room-review-grid");
  const selectedSources = roomChoices.filter((choice) => isClimateSource(choice) && choice.device.selected === true);
  const selectedControls = controlChoices.filter((choice) => choice.device.selected === true);
  const physicalControlIds = new Set(selectedControls.map((choice) => (
    choice.candidate.device_group_id || choice.candidate.candidate_id
  )));
  const manualChannels = selectedControls.filter((choice) => (
    ACTIVE_DEVICE_TYPES.has(choice.type) && choice.device.channel === "universal_ir"
  ));
  const testableChannels = selectedControls.filter((choice) => (
    ACTIVE_DEVICE_TYPES.has(choice.type) && choice.device.channel !== "universal_ir"
  ));
  const confirmedChannels = testableChannels.filter((choice) => (
    choice.device.channelTest && choice.device.channelTest.status === "confirmed"
  ));
  const failedChannels = testableChannels.filter((choice) => (
    choice.device.channelTest && choice.device.channelTest.status === "failed"
  ));
  const sourceSummary = ["temperature_sensor", "humidity_sensor"].every((type) => (
    selectedSources.filter((choice) => choice.type === type).length === 1
  )) ? "Температура и влажность выбраны" : "Нужно выбрать оба показания";
  const channelSummary = testableChannels.length
    ? `${confirmedChannels.length} из ${testableChannels.length} подтверждено`
    : (manualChannels.length ? "ИК-канал · ручная проверка" : "Нет проверяемых каналов");
  [
    ["Дневная цель", `${state.day.temperature || "—"} °C · ${state.day.humidity || "—"} %`],
    ["Ночная цель", `${state.night.temperature || "—"} °C · ${state.night.humidity || "—"} %`],
    ["Главные показания", sourceSummary],
    ["Устройства управления", physicalControlIds.size ? `${physicalControlIds.size} выбрано` : "Не выбраны"],
    ["Проверка каналов", channelSummary],
    ["Режим дня", this._firstRun.schedule.enabled ? "Автоматический" : "Ручной"],
  ].forEach(([label, value]) => {
    const item = el("div", "room-review-item");
    item.appendChild(el("span", null, label));
    item.appendChild(el("strong", null, value));
    reviewGrid.appendChild(item);
  });
  reviewSection.appendChild(reviewGrid);
  if (failedChannels.length) {
    reviewSection.appendChild(el(
      "div",
      "wizard-warning room-review-channel-warning",
      `Не прошли проверку: ${failedChannels.length}. Вернитесь к устройствам, проверьте выбранный канал и повторите тест.`
    ));
  } else if (manualChannels.length || confirmedChannels.length < testableChannels.length) {
    const guidance = [];
    if (confirmedChannels.length < testableChannels.length) {
      guidance.push("Каналы можно сохранить без теста, но перед включением автоматического управления лучше подтвердить связь в карточке каждого устройства.");
    }
    if (manualChannels.length) {
      guidance.push("Для ИК-канала отправьте пробный код и подтвердите физическую реакцию устройства вручную.");
    }
    reviewSection.appendChild(el(
      "div",
      "wizard-hint room-review-channel-hint",
      guidance.join(" ")
    ));
  }
  if (report) {
    const reportBox = el("div", "wizard-report");
    reportBox.appendChild(el("strong", null, roomReady ? "Комната проверена" : "Проверка требует внимания"));
    const issues = report.issues || [];
    if (issues.length) {
      const list = el("ul");
      issues.forEach((issue) => list.appendChild(el(
        "li",
        issue.level === "warning" ? "issue-warning" : null,
        issue.message || "Проверьте настройки комнаты."
      )));
      reportBox.appendChild(list);
    } else if (roomReady) {
      reportBox.appendChild(el("div", "muted", "Все выбранные устройства и цели прошли проверку."));
    }
    reviewSection.appendChild(reportBox);
  }
  if (this._activeRoomSetupPane === "review") card.appendChild(reviewSection);
  const actions = el("div", "actions room-setup-footer");
  const back = el("button", "secondary", "Назад к списку комнат");
  back.disabled = this._busy;
  back.addEventListener("click", () => this._firstRunBackToRooms());
  actions.appendChild(back);
  const stepActions = el("span", "room-setup-footer-steps");
  if (activePaneIndex > 0) {
    const previous = el("button", "secondary", "Назад");
    previous.type = "button";
    previous.disabled = this._busy;
    previous.addEventListener("click", () => {
      this._activeRoomSetupPane = ROOM_SETUP_PANES[activePaneIndex - 1].id;
      this._render();
    });
    stepActions.appendChild(previous);
  }
  if (activePaneIndex < ROOM_SETUP_PANES.length - 1) {
    const next = el("button", null, "Продолжить");
    next.type = "button";
    next.disabled = this._busy;
    next.addEventListener("click", () => {
      this._activeRoomSetupPane = ROOM_SETUP_PANES[activePaneIndex + 1].id;
      this._render();
    });
    stepActions.appendChild(next);
  } else {
    if (roomReady) {
      const finish = el("button", null, "Завершить");
      finish.type = "button";
      finish.disabled = this._busy;
      finish.title = "Завершить настройку комнаты и вернуться к списку комнат.";
      finish.addEventListener("click", () => this._firstRunBackToRooms());
      stepActions.appendChild(finish);
    } else {
      const check = el("button", null, "Проверить комнату");
      check.disabled = this._busy || room.selectable !== true;
      check.title = check.disabled ? "Комната недоступна для настройки." : "Проверить цели и выбранные привязки.";
      check.addEventListener("click", () => this._checkFirstRunRoom(room.id));
      stepActions.appendChild(check);
    }
  }
  actions.appendChild(stepActions);
  card.appendChild(actions);
  fields.maxTemperature = maxTemperature;
  fields.minTemperature = minTemperature;
  fields.room = room;
  this._firstRunFields = { room: fields };
}
