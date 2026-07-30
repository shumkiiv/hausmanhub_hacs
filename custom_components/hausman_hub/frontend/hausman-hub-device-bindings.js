const DEVICE_BINDINGS_API = "hausman_hub/v1/admin/climate-device-bindings";
const DEVICE_BINDINGS_PREVIEW_API = `${DEVICE_BINDINGS_API}/preview`;

export function renderDeviceBindingCallout(panel, container, helpers) {
  const { el, svgIcon } = helpers;
  const bindings = el("section", "card native-binding-callout");
  bindings.appendChild(svgIcon("device", "native-binding-callout-icon"));
  const copy = el("div", "native-binding-callout-copy");
  copy.appendChild(el("strong", null, "Нативные сущности устройств"));
  copy.appendChild(el("p", "muted", "После миграции некоторым устройствам нужно один раз указать точную сущность Home Assistant. Выбор сначала проверяется и не отправляет команды."));
  bindings.appendChild(copy);
  const open = el("button", "secondary", "Открыть привязки");
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

export async function loadDeviceBindings(panel, force = false) {
  const state = panel._deviceBindings;
  if (!panel._hass || state.loading || (isDirty(panel) && !force)) return;
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
  copy.appendChild(el("h3", null, "Привязка к сущностям Home Assistant"));
  copy.appendChild(el("p", "muted settings-card-intro", "Для каждого логического устройства выберите одну настоящую сущность того же типа и той же комнаты. HausmanHub не выбирает дубли автоматически."));
  head.appendChild(copy);
  const refresh = el("button", "secondary", "Обновить список");
  refresh.disabled = panel._busy || state.loading || isDirty(panel);
  refresh.title = isDirty(panel) ? "Сначала сохраните или отмените текущий выбор." : "";
  refresh.addEventListener("click", () => loadDeviceBindings(panel, true));
  head.appendChild(refresh);
  intro.appendChild(head);
  const metrics = el("div", "native-binding-metrics");
  metrics.appendChild(metric(el, "Устройства", Number(summary.device_count) || 0, ""));
  metrics.appendChild(metric(el, "Привязано", Number(summary.bound_count) || 0, "is-ready"));
  metrics.appendChild(metric(el, "Нужно привязать", Number(summary.missing_count) || 0, Number(summary.missing_count) ? "is-warning" : "is-ready"));
  intro.appendChild(metrics);
  intro.appendChild(el("div", "settings-explainer", "Сначала нажмите «Проверить». Сохранение станет доступно только для однозначных доступных сущностей в той же комнате. Проверка и сохранение не включают устройства и не меняют их режимы."));
  const otherRooms = el("label", "native-binding-other-rooms");
  const otherCopy = el("span");
  otherCopy.appendChild(el("strong", null, "Показывать сущности из других комнат"));
  otherCopy.appendChild(el("small", null, "Только для поиска ошибок. Сохранить такую привязку нельзя, пока комната не исправлена в Home Assistant."));
  const toggle = el("input", "settings-toggle");
  toggle.type = "checkbox";
  toggle.checked = state.showOtherRooms;
  toggle.addEventListener("change", () => {
    state.showOtherRooms = toggle.checked;
    panel._render();
  });
  otherRooms.appendChild(otherCopy);
  otherRooms.appendChild(toggle);
  intro.appendChild(otherRooms);
  container.appendChild(intro);
}

function renderBindingDeviceRow(panel, list, device, helpers, usedSelections) {
  const { el } = helpers;
  const state = panel._deviceBindings;
  const row = el("article", "native-binding-row");
  const identity = el("div", "native-binding-identity");
  identity.appendChild(el("strong", null, device.name));
  identity.appendChild(el("small", null, device.kind_name));
  identity.appendChild(el("code", null, device.device_id));
  row.appendChild(identity);
  const field = el("label", "native-binding-field");
  field.appendChild(el("span", null, "Сущность Home Assistant"));
  const select = el("select");
  const empty = el("option", null, "Выберите сущность");
  empty.value = "";
  select.appendChild(empty);
  const currentValue = String(state.selections[device.device_id] || "");
  (device.candidates || []).forEach((candidate) => {
    if (!candidate.same_room && !state.showOtherRooms && candidate.entity_id !== currentValue) return;
    const option = el("option", null, [candidate.name, candidate.room_name,
      candidate.available ? "доступно" : "недоступно", candidate.entity_id].join(" · "));
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
  });
  field.appendChild(select);
  const selectedCandidate = (device.candidates || []).find((candidate) => candidate.entity_id === currentValue);
  field.appendChild(el("small", "native-binding-help", currentValue
    ? (selectedCandidate?.same_room
      ? "Тип и комната совпадают. Выполните проверку перед сохранением."
      : "Текущая сущность отсутствует или относится к другой комнате.")
    : "Привязка отсутствует: устройство пока нельзя наблюдать нативно."));
  row.appendChild(field);
  row.appendChild(currentValue
    ? el("span", `native-binding-state ${selectedCandidate?.available ? "is-ready" : "is-warning"}`, selectedCandidate?.available ? "Выбрано" : "Недоступно")
    : el("span", "native-binding-state is-warning", "Не привязано"));
  list.appendChild(row);
}

function renderBindingRoom(panel, container, room, helpers, usedSelections) {
  const { el } = helpers;
  const section = el("section", "card settings-card native-binding-room");
  const head = el("div", "native-binding-room-head");
  const copy = el("div");
  copy.appendChild(el("h3", null, room.name || room.id));
  const missing = (room.devices || []).filter((device) => !device.current_entity_id).length;
  copy.appendChild(el("p", "muted", missing ? `Без нативной сущности: ${missing}` : "Все устройства этой комнаты привязаны"));
  head.appendChild(copy);
  head.appendChild(el("span", `status-badge ${missing ? "is-warning" : "is-ready"}`, missing ? "Нужна настройка" : "Готово"));
  section.appendChild(head);
  const list = el("div", "native-binding-list");
  (room.devices || []).forEach((device) => renderBindingDeviceRow(panel, list, device, helpers, usedSelections));
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
  actions.appendChild(el("span", "settings-actions-state", isDirty(panel) ? "Есть непроверенные изменения" : "Изменений нет"));
  const reset = el("button", "secondary", "Отменить выбор");
  reset.disabled = panel._busy || !isDirty(panel);
  reset.addEventListener("click", () => {
    state.selections = {};
    devices(panel).forEach((device) => { state.selections[device.device_id] = device.current_entity_id || ""; });
    state.preview = null;
    state.status = "";
    panel._render();
  });
  const check = el("button", "secondary", "Проверить");
  check.disabled = panel._busy || !changes(panel).length;
  check.addEventListener("click", () => preview(panel));
  const saveButton = el("button", null, "Сохранить привязки");
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
