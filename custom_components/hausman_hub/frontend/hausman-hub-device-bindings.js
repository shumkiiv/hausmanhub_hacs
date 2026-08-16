const DEVICE_BINDINGS_API = "hausman_hub/v1/admin/climate-device-bindings";
const DEVICE_BINDINGS_PREVIEW_API = `${DEVICE_BINDINGS_API}/preview`;
const AUTO_PREVIEW_DELAY_MS = 350;

export function renderDeviceBindingCallout(panel, container, helpers) {
  const { el, svgIcon } = helpers;
  const bindings = el("section", "card native-binding-callout");
  bindings.appendChild(svgIcon("device", "native-binding-callout-icon"));
  const copy = el("div", "native-binding-callout-copy");
  copy.appendChild(el("strong", null, "Восстановление связей устройств"));
  copy.appendChild(el("p", "muted", "Служебный раздел нужен только после переноса или переименования сущностей. В обычной работе открывать его не требуется."));
  bindings.appendChild(copy);
  const open = el("button", "secondary", "Проверить связи");
  open.addEventListener("click", () => panel._activateSettingsView("bindings"));
  bindings.appendChild(open);
  container.appendChild(bindings);
}

function devices(panel) {
  return (panel._deviceBindings.data && panel._deviceBindings.data.rooms || [])
    .flatMap((room) => room.devices || []);
}

function isDirty(panel) {
  return devices(panel).some((device) => (
    String(panel._deviceBindings.selections[device.device_id] || "")
    !== String(device.current_entity_id || "")
  ));
}

function changes(panel) {
  return devices(panel).flatMap((device) => {
    const selected = String(panel._deviceBindings.selections[device.device_id] || "");
    if (!selected || selected === String(device.current_entity_id || "")) return [];
    return [{ device_id: device.device_id, entity_id: selected }];
  });
}

function cancelScheduledPreview(state) {
  if (state.previewTimer === null) return;
  clearTimeout(state.previewTimer);
  state.previewTimer = null;
}

function schedulePreview(panel) {
  const state = panel._deviceBindings;
  cancelScheduledPreview(state);
  if (!changes(panel).length) return;
  state.previewTimer = setTimeout(() => {
    state.previewTimer = null;
    preview(panel);
  }, AUTO_PREVIEW_DELAY_MS);
}

export async function loadDeviceBindings(panel, force = false) {
  const state = panel._deviceBindings;
  if (!panel._hass || state.loading || (isDirty(panel) && !force)) return;
  cancelScheduledPreview(state);
  state.loading = true;
  state.error = false;
  panel._render();
  try {
    const data = await panel._hass.callApi("GET", DEVICE_BINDINGS_API);
    state.data = data;
    state.selections = {};
    (data.rooms || []).forEach((room) => (room.devices || []).forEach((device) => {
      state.selections[device.device_id] = device.current_entity_id || "";
    }));
    state.preview = null;
    state.status = "";
  } catch (error) {
    state.error = true;
  } finally {
    state.loading = false;
    panel._render();
  }
}

async function preview(panel) {
  const selected = changes(panel);
  const state = panel._deviceBindings;
  cancelScheduledPreview(state);
  if (panel._busy || !state.data || !selected.length) return;
  panel._busy = true;
  state.status = "";
  state.preview = null;
  panel._render();
  try {
    state.preview = await panel._hass.callApi("POST", DEVICE_BINDINGS_PREVIEW_API, {
      snapshot_revision: state.data.snapshot_revision,
      bindings: selected,
    });
    state.status = state.preview.save_allowed
      ? `Проверено: можно сохранить ${selected.length} ${selected.length === 1 ? "привязку" : "привязки"}.`
      : "Проверка нашла замечания. Исправьте выбор и повторите.";
  } catch (error) {
    state.status = error && error.status === 409
      ? "Список сущностей изменился. Обновите его и повторите выбор."
      : "Не удалось проверить привязки. Выбор сохранён на экране.";
  } finally {
    panel._busy = false;
    panel._render();
  }
}

async function save(panel) {
  const selected = changes(panel);
  const state = panel._deviceBindings;
  cancelScheduledPreview(state);
  if (panel._busy || !state.data || !selected.length || state.preview?.save_allowed !== true) return;
  panel._busy = true;
  state.status = "Сохраняю привязки…";
  panel._render();
  try {
    const receipt = await panel._hass.callApi("POST", DEVICE_BINDINGS_API, {
      snapshot_revision: state.data.snapshot_revision,
      preview_revision: state.preview.preview_revision,
      bindings: selected,
    });
    panel._notice = `Привязки сохранены: ${Number(receipt.updated_devices) || selected.length}. Команды устройствам не отправлялись.`;
    state.data = null;
    state.selections = {};
    state.preview = null;
    state.status = "";
    await panel._load();
  } catch (error) {
    if (error && error.status === 409) state.preview = null;
    state.status = error && error.status === 409
      ? "Данные изменились после проверки. Обновите список и проверьте заново."
      : "Сохранить привязки не удалось. Никакие команды устройствам не отправлены.";
  } finally {
    panel._busy = false;
    if (!state.data) await loadDeviceBindings(panel, true);
    else panel._render();
  }
}

function metric(el, label, value, tone) {
  const node = el("div", `native-binding-metric ${tone}`);
  node.appendChild(el("strong", null, value));
  node.appendChild(el("span", null, label));
  return node;
}

function renderSummary(panel, container, helpers) {
  const { el, svgIcon } = helpers;
  const state = panel._deviceBindings;
  const summary = state.data.summary || {};
  const intro = el("section", "card settings-card native-binding-summary");
  const head = el("div", "native-binding-summary-head");
  const copy = el("div");
  copy.appendChild(el("div", "settings-heading-eyebrow", "Служебное восстановление"));
  copy.appendChild(el("h3", null, "Связи с устройствами Home Assistant"));
  copy.appendChild(el("p", "muted settings-card-intro", "Hausman Hub помнит назначение устройства, но после переноса или переименования может потерять ссылку на его сущность. Здесь ссылка восстанавливается — сами устройства и их настройки не меняются."));
  head.appendChild(copy);
  const refresh = el("button", "secondary", "Обновить список");
  refresh.disabled = panel._busy || state.loading || isDirty(panel);
  refresh.title = isDirty(panel) ? "Сначала сохраните или отмените текущий выбор." : "";
  refresh.addEventListener("click", () => loadDeviceBindings(panel, true));
  head.appendChild(refresh);
  intro.appendChild(head);
  const metrics = el("div", "native-binding-metrics");
  metrics.appendChild(metric(el, "Связей всего", Number(summary.device_count) || 0, ""));
  metrics.appendChild(metric(el, "Работают", Number(summary.bound_count) || 0, "is-ready"));
  metrics.appendChild(metric(el, "Нужно восстановить", Number(summary.missing_count) || 0, Number(summary.missing_count) ? "is-warning" : "is-ready"));
  intro.appendChild(metrics);
  const steps = el("div", "native-binding-explanation");
  [
    ["1", "Найдите назначение", "Например: кондиционер гостиной или главный датчик температуры."],
    ["2", "Выберите сущность", "Hausman Hub покажет только подходящий тип и сначала — ту же комнату."],
    ["3", "Проверьте и сохраните", "Проверка безопасна: команды устройствам не отправляются."],
  ].forEach(([number, title, text]) => {
    const item = el("div", "native-binding-explanation-item");
    item.appendChild(el("span", null, number));
    const itemCopy = el("div");
    itemCopy.appendChild(el("strong", null, title));
    itemCopy.appendChild(el("small", null, text));
    item.appendChild(itemCopy);
    steps.appendChild(item);
  });
  intro.appendChild(steps);
  const scope = el("div", "native-binding-scope");
  const scopeCopy = el("div");
  scopeCopy.appendChild(el("strong", null, "Где искать подходящее устройство"));
  scopeCopy.appendChild(el("small", null, state.showOtherRooms
    ? "Диагностический режим показывает весь дом. Связь из другой комнаты сохранить нельзя, пока комната не исправлена в Home Assistant."
    : "Обычный режим показывает только доступные сущности из той же комнаты."));
  scope.appendChild(scopeCopy);
  const scopeChoice = el("div", "native-binding-scope-choice");
  [
    [false, "В этой комнате"],
    [true, "Во всём доме"],
  ].forEach(([value, label]) => {
    const button = el("button", state.showOtherRooms === value ? "is-current" : "", label);
    button.type = "button";
    button.addEventListener("click", () => {
      state.showOtherRooms = value;
      panel._render();
    });
    scopeChoice.appendChild(button);
  });
  scope.appendChild(scopeChoice);
  intro.appendChild(scope);
  if (Number(summary.bound_count) > 0) {
    const configured = el("button", "secondary native-binding-configured-toggle",
      state.showConfigured ? "Скрыть работающие связи" : `Показать работающие связи · ${Number(summary.bound_count)}`);
    configured.type = "button";
    configured.addEventListener("click", () => {
      state.showConfigured = !state.showConfigured;
      panel._render();
    });
    intro.appendChild(configured);
  }
  container.appendChild(intro);
}

function purposeFor(device) {
  const kind = String(device.kind_name || "").toLowerCase();
  if (kind.includes("температур")) return "По этой сущности Hausman Hub получает температуру комнаты.";
  if (kind.includes("влажност")) return "По этой сущности Hausman Hub получает влажность комнаты.";
  if (kind.includes("кондиционер")) return "Через эту сущность Hausman Hub управляет режимом и температурой кондиционера.";
  if (kind.includes("термоголов")) return "Через эту сущность Hausman Hub управляет отоплением радиатора.";
  if (kind.includes("увлажн")) return "Через эту сущность Hausman Hub управляет увлажнителем.";
  return "Эта связь нужна Hausman Hub для наблюдения или управления устройством.";
}

function renderBindingDeviceRow(panel, list, device, helpers, usedSelections) {
  const { el } = helpers;
  const state = panel._deviceBindings;
  const row = el("article", "native-binding-row");
  const identity = el("div", "native-binding-identity");
  identity.appendChild(el("span", "native-binding-label", "Что должно работать"));
  identity.appendChild(el("strong", null, device.name));
  identity.appendChild(el("small", null, device.kind_name));
  identity.appendChild(el("p", "muted", purposeFor(device)));
  row.appendChild(identity);
  const field = el("label", "native-binding-field");
  field.appendChild(el("span", null, "Подходящее устройство в Home Assistant"));
  const currentValue = String(state.selections[device.device_id] || "");
  const suitable = (device.candidates || []).filter((candidate) => (
    candidate.same_room && candidate.available
    && (!usedSelections.has(candidate.entity_id) || candidate.entity_id === currentValue)
  ));
  if (!currentValue && suitable.length === 1) {
    const recommendation = el("div", "native-binding-recommendation");
    const recommendationCopy = el("div");
    recommendationCopy.appendChild(el("strong", null, suitable[0].name));
    recommendationCopy.appendChild(el("small", null, "Единственное доступное совпадение по назначению и комнате."));
    recommendation.appendChild(recommendationCopy);
    const choose = el("button", "secondary", "Выбрать");
    choose.type = "button";
    choose.disabled = panel._busy;
    choose.addEventListener("click", (event) => {
      event?.preventDefault?.();
      state.selections[device.device_id] = suitable[0].entity_id;
      state.preview = null;
      state.status = "";
      panel._render();
      schedulePreview(panel);
    });
    recommendation.appendChild(choose);
    field.appendChild(recommendation);
  } else if (!currentValue && suitable.length > 1) {
    field.appendChild(el("small", "native-binding-choice-note", `Найдено несколько совпадений: ${suitable.length}. Сверьте название устройства.`));
  }
  const select = el("select");
  const empty = el("option", null, "Выберите устройство");
  empty.value = "";
  select.appendChild(empty);
  (device.candidates || []).forEach((candidate) => {
    if (!candidate.same_room && !state.showOtherRooms && candidate.entity_id !== currentValue) return;
    const option = el("option", null, [candidate.name, candidate.room_name,
      candidate.available ? "доступно" : "нет связи"].filter(Boolean).join(" · "));
    option.value = candidate.entity_id;
    option.disabled = !candidate.available || !candidate.same_room
      || (usedSelections.has(candidate.entity_id) && candidate.entity_id !== currentValue);
    select.appendChild(option);
  });
  select.value = currentValue;
  select.disabled = panel._busy;
  select.addEventListener("change", () => {
    state.selections[device.device_id] = select.value;
    state.preview = null;
    state.status = "";
    panel._render();
    schedulePreview(panel);
  });
  field.appendChild(select);
  const selectedCandidate = (device.candidates || []).find((candidate) => candidate.entity_id === currentValue);
  field.appendChild(el("small", "native-binding-help", currentValue
    ? (selectedCandidate?.same_room
      ? "Комната и назначение совпадают. Осталось проверить связь перед сохранением."
      : "Выбранная сущность относится к другой комнате. Сначала исправьте комнату в Home Assistant.")
    : "Связь не выбрана — эта функция Hausman Hub сейчас не работает."));
  row.appendChild(field);
  row.appendChild(currentValue
    ? el("span", `native-binding-state ${selectedCandidate?.available ? "is-ready" : "is-warning"}`, selectedCandidate?.available ? "Связь выбрана" : "Нет связи")
    : el("span", "native-binding-state is-warning", "Нужно выбрать"));
  list.appendChild(row);
}

function renderBindingRoom(panel, container, room, helpers, usedSelections) {
  const { el } = helpers;
  const section = el("section", "card settings-card native-binding-room");
  const head = el("div", "native-binding-room-head");
  const copy = el("div");
  copy.appendChild(el("h3", null, room.name || room.id));
  const allDevices = room.devices || [];
  const missing = allDevices.filter((device) => !device.current_entity_id).length;
  copy.appendChild(el("p", "muted", missing ? `Нужно восстановить связей: ${missing}` : "Все связи этой комнаты работают"));
  head.appendChild(copy);
  head.appendChild(el("span", `status-badge ${missing ? "is-warning" : "is-ready"}`, missing ? "Нужна настройка" : "Готово"));
  section.appendChild(head);
  const list = el("div", "native-binding-list");
  const visible = panel._deviceBindings.showConfigured
    ? allDevices : allDevices.filter((device) => !device.current_entity_id);
  visible.forEach((device) => renderBindingDeviceRow(panel, list, device, helpers, usedSelections));
  if (!visible.length) {
    const ready = el("div", "native-binding-room-ready");
    ready.appendChild(el("strong", null, "Настройка не требуется"));
    ready.appendChild(el("small", "muted", "Все связи работают. Их можно показать кнопкой выше."));
    list.appendChild(ready);
  }
  section.appendChild(list);
  container.appendChild(section);
}

function renderActions(panel, container, helpers) {
  const { el } = helpers;
  const state = panel._deviceBindings;
  const issues = state.preview && state.preview.issues || [];
  if (issues.length || state.status) {
    const feedback = el("div", `native-binding-feedback ${issues.length ? "is-warning" : "is-ready"}`);
    if (state.status) feedback.appendChild(el("strong", null, state.status));
    issues.forEach((issue) => feedback.appendChild(el("p", null, issue.message)));
    container.appendChild(feedback);
  }
  const actions = el("div", "settings-page-actions native-binding-actions");
  const dirty = isDirty(panel);
  const readyToSave = dirty && state.preview?.save_allowed === true;
  const actionStatus = readyToSave
    ? el("span", "settings-actions-state is-ready", "Проверка пройдена — можно сохранить")
    : dirty
      ? el("span", "settings-actions-state is-warning", "Есть изменения — требуется проверка")
      : el("span", "settings-actions-state", "Все привязки сохранены");
  actions.appendChild(actionStatus);
  const reset = el("button", "secondary", "Отменить изменения");
  reset.disabled = panel._busy || !isDirty(panel);
  reset.addEventListener("click", () => {
    cancelScheduledPreview(state);
    state.selections = {};
    devices(panel).forEach((device) => { state.selections[device.device_id] = device.current_entity_id || ""; });
    state.preview = null;
    state.status = "";
    panel._render();
  });
  const check = el("button", "secondary", "Проверить изменения");
  check.disabled = panel._busy || !changes(panel).length;
  check.addEventListener("click", () => {
    cancelScheduledPreview(state);
    preview(panel);
  });
  const saveButton = el("button", null, "Сохранить в Home Assistant");
  saveButton.disabled = panel._busy || state.preview?.save_allowed !== true;
  saveButton.addEventListener("click", () => save(panel));
  actions.appendChild(reset);
  actions.appendChild(check);
  actions.appendChild(saveButton);
  container.appendChild(actions);
}

export function renderDeviceBindings(panel, container, helpers) {
  const { el } = helpers;
  const state = panel._deviceBindings;
  if (state.loading && !state.data) {
    container.appendChild(el("section", "card settings-card native-binding-loading", "Загружаю устройства и сущности Home Assistant…"));
    return;
  }
  if (state.error || !state.data) {
    const failure = el("section", "card settings-card native-binding-empty");
    failure.appendChild(el("h3", null, "Не удалось получить список привязок"));
    failure.appendChild(el("p", "muted", "Проверьте доступность Home Assistant и обновите список. Сохранённые настройки не изменены."));
    const retry = el("button", "secondary", "Повторить");
    retry.disabled = state.loading;
    retry.addEventListener("click", () => loadDeviceBindings(panel, true));
    failure.appendChild(retry);
    container.appendChild(failure);
    return;
  }
  renderSummary(panel, container, helpers);
  const usedSelections = new Set(Object.values(state.selections).filter(Boolean));
  (state.data.rooms || []).forEach((room) => renderBindingRoom(panel, container, room, helpers, usedSelections));
  renderActions(panel, container, helpers);
}
