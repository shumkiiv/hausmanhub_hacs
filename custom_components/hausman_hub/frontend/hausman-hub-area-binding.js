function groupName(group) {
  const first = group[0] || {};
  return first.device_name || first.name || first.candidate_id || "Устройство";
}

function groupCanMove(group) {
  return group.length > 0 && group.every(
    (candidate) => candidate.configured !== true
      && ["available", "unavailable"].includes(candidate.status)
  );
}

function duplicateNames(groups, normalizedText) {
  const counts = new Map();
  groups.forEach((group) => {
    const name = normalizedText(groupName(group));
    if (name) counts.set(name, (counts.get(name) || 0) + 1);
  });
  return counts;
}

function renderDeviceRow(panel, group, rooms, options, deps, nameCounts) {
  const { el, normalizedText, selectField, svgIcon, imagePattern } = deps;
  const first = group[0];
  const virtualYandex = panel._firstRunIsYandexVirtual(first);
  const available = panel._firstRunGroupAvailable(group);
  const displayName = virtualYandex
    ? (first.name || groupName(group))
    : groupName(group);
  const duplicateName = (nameCounts.get(normalizedText(groupName(group))) || 0) > 1;
  const row = el("article", "binding-device-row");
  if (virtualYandex) row.className += available
    ? " is-virtual" : " is-virtual is-unavailable";

  const thumb = el("div", "device-thumb");
  const fallback = el("span", "device-thumb-fallback");
  fallback.appendChild(svgIcon("device"));
  if (first.image_url && imagePattern.test(first.image_url)) {
    const image = el("img");
    image.src = first.image_url;
    image.alt = "";
    image.addEventListener("error", () => {
      image.hidden = true;
      fallback.hidden = false;
    });
    fallback.hidden = true;
    thumb.appendChild(image);
  }
  thumb.appendChild(fallback);
  row.appendChild(thumb);

  const copy = el("div", "binding-device-copy");
  copy.appendChild(el("strong", null, displayName));
  const details = virtualYandex
    ? `Виртуальное устройство Яндекса · № ${panel._firstRunPublicIdentity(first)}`
    : [first.manufacturer, first.model].filter(Boolean).join(" · ");
  if (details) copy.appendChild(el("small", "muted", details));
  const types = new Set();
  group.forEach((candidate) => (
    candidate.suggested_types || []
  ).forEach((type) => types.add(type)));
  const typeNames = (options.display_names || {}).device_types || {};
  const chips = el("div", "device-card-chips");
  types.forEach((type) => chips.appendChild(el("span", "chip", typeNames[type] || type)));
  copy.appendChild(chips);
  if (virtualYandex) {
    copy.appendChild(el(
      "span",
      `status-badge ${available ? "is-ready" : "is-attention"}`,
      available ? "Доступно" : "Недоступно",
    ));
    if (duplicateName) {
      copy.appendChild(el(
        "small",
        "virtual-device-hint",
        available
          ? "Имя совпадает с другим виртуальным устройством. Проверьте объект перед сменой комнаты."
          : "Недоступный объект с таким же именем может быть устаревшей виртуальной сущностью. Проверьте его в Home Assistant.",
      ));
    }
  }
  row.appendChild(copy);

  const picker = selectField(
    [{ label: "Не привязано", value: "" }].concat(
      rooms.filter((room) => room.selectable === true).map((room) => ({
        label: room.name || room.id,
        value: room.id,
      })),
    ),
    panel._firstRunGroupRoom(group),
    () => {
      panel._assignFirstRunGroup(group, picker.value);
      panel._render();
    },
  );
  picker.disabled = panel._busy || !groupCanMove(group);
  const pickerLabel = el("label", "binding-room-picker");
  pickerLabel.appendChild(el("span", "binding-room-picker-label", "Комната Home Assistant"));
  pickerLabel.appendChild(picker);
  if (picker.disabled) {
    pickerLabel.appendChild(el(
      "small",
      "muted",
      "Устройство уже используется контуром или недоступно для безопасного переноса.",
    ));
  }
  row.appendChild(pickerLabel);
  return row;
}

function renderDeviceList(panel, groups, rooms, options, deps, emptyText) {
  const list = deps.el("div", "binding-device-list");
  const names = duplicateNames(groups, deps.normalizedText);
  groups.forEach((group) => list.appendChild(
    renderDeviceRow(panel, group, rooms, options, deps, names)
  ));
  if (!groups.length) {
    list.appendChild(deps.el("div", "card empty-state muted", emptyText));
  }
  return list;
}

export function renderFirstRunAreaBinding(card, helpers) {
  const { el, normalizedText, selectField, svgIcon, ZIGBEE2MQTT_IMAGE_PATTERN } = helpers;
  const deps = {
    el,
    normalizedText,
    selectField,
    svgIcon,
    imagePattern: ZIGBEE2MQTT_IMAGE_PATTERN,
  };
  const options = this._firstRun.options;
  const rooms = options.rooms || [];
  const allGroups = this._firstRunPhysicalGroups(this._firstRunAreaCandidates());
  const roomlessGroups = allGroups.filter((group) => !group[0].room_id);
  const assignedGroups = allGroups.filter((group) => Boolean(group[0].room_id));

  card.appendChild(el("h2", null, "Привязка комнат и устройств"));
  card.appendChild(el(
    "div",
    "section-intro",
    "Комната устройства хранится только в Home Assistant. Здесь можно подготовить несколько изменений и сохранить их одной операцией.",
  ));
  const summary = el("div", "binding-summary");
  [
    ["Комнат", rooms.length],
    ["Устройств", allGroups.length],
    ["Без комнаты", roomlessGroups.length],
  ].forEach(([label, value]) => {
    const metric = el("div");
    metric.appendChild(el("strong", null, value));
    metric.appendChild(el("span", "muted", label));
    summary.appendChild(metric);
  });
  card.appendChild(summary);

  const tools = el("div", "binding-tools");
  const refresh = el("button", "secondary", "Обновить список устройств");
  refresh.disabled = this._busy || this._firstRun.loading;
  refresh.addEventListener("click", () => this._loadFirstRunOptions(true));
  tools.appendChild(refresh);
  const showDevices = el("input");
  showDevices.type = "checkbox";
  showDevices.checked = this._firstRun.showRoomDevices === true;
  const showLabel = el("label", "checkbox-field");
  showLabel.appendChild(showDevices);
  showLabel.appendChild(el("span", null, "Показать устройства, уже привязанные к комнатам"));
  tools.appendChild(showLabel);
  card.appendChild(tools);

  card.appendChild(el("h3", "binding-heading", "Комнаты"));
  const fields = { rooms: {} };
  const roomList = el("div", "first-run-room-list");
  rooms.forEach((room) => {
    const state = this._firstRunRoomState(room);
    const groups = this._firstRunPhysicalGroups(this._firstRunRoomCandidates(room.id));
    const roomCard = el("article", "card first-run-room");
    const heading = el("div", "binding-room-heading");
    heading.appendChild(el("h3", null, room.name || room.id));
    heading.appendChild(el("span", "status-badge", `${groups.length} устр.`));
    roomCard.appendChild(heading);
    const include = el("input");
    include.type = "checkbox";
    include.checked = state.included;
    include.disabled = room.selectable !== true || this._busy;
    const includeLabel = el("label", "checkbox-field");
    includeLabel.appendChild(include);
    includeLabel.appendChild(el("span", null, "Использовать в климатическом контуре"));
    roomCard.appendChild(includeLabel);
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
    roomList.appendChild(roomCard);
  });
  if (!rooms.length) {
    roomList.appendChild(el(
      "div",
      "card empty-state muted",
      "Home Assistant пока не передал ни одной комнаты.",
    ));
  }
  card.appendChild(roomList);

  card.appendChild(el("h3", "binding-heading", "Устройства без комнаты"));
  card.appendChild(el(
    "div",
    "muted binding-help",
    "Выберите комнату. До нажатия «Сохранить привязки» реестр Home Assistant не изменится.",
  ));
  card.appendChild(renderDeviceList(
    this,
    roomlessGroups,
    rooms,
    options,
    deps,
    "Все найденные устройства уже распределены по комнатам.",
  ));

  if (this._firstRun.showRoomDevices) {
    card.appendChild(el("h3", "binding-heading", "Устройства в комнатах"));
    card.appendChild(el(
      "div",
      "muted binding-help",
      "Можно перенести устройство в другую комнату или убрать привязку. Устройства действующего климатического контура защищены от случайного переноса.",
    ));
    card.appendChild(renderDeviceList(
      this,
      assignedGroups,
      rooms,
      options,
      deps,
      "В комнатах пока нет доступных устройств.",
    ));
  }

  const pendingAssignments = Object.keys(this._firstRun.areaAssignments).length;
  const saveBar = el("div", "binding-save-bar");
  const saveCopy = el("div", "binding-save-copy");
  saveCopy.appendChild(el(
    "strong",
    null,
    pendingAssignments
      ? `Подготовлено изменений: ${pendingAssignments}`
      : "Нет несохранённых привязок",
  ));
  saveCopy.appendChild(el(
    "small",
    "muted",
    pendingAssignments
      ? "Изменения попадут в Home Assistant только после сохранения."
      : "Выберите другую комнату у нужного устройства.",
  ));
  saveBar.appendChild(saveCopy);
  const saveActions = el("div", "actions compact-actions");
  const clear = el("button", "secondary", "Сбросить выбор");
  clear.disabled = this._busy || pendingAssignments === 0;
  clear.addEventListener("click", () => {
    this._firstRun.areaAssignments = {};
    this._firstRun.areaSaveError = "";
    this._firstRun.areaSaveStatus = "";
    this._render();
  });
  const save = el("button", null, "Сохранить привязки в Home Assistant");
  save.disabled = this._busy || pendingAssignments === 0;
  save.addEventListener("click", () => this._saveFirstRunAreaAssignments());
  saveActions.appendChild(clear);
  saveActions.appendChild(save);
  saveBar.appendChild(saveActions);
  if (this._firstRun.areaSaveStatus) {
    saveBar.appendChild(el("div", "binding-save-status", this._firstRun.areaSaveStatus));
  }
  if (this._firstRun.areaSaveError) {
    saveBar.appendChild(el("div", "field-error binding-save-error", this._firstRun.areaSaveError));
  }
  card.appendChild(saveBar);
  showDevices.addEventListener("change", () => {
    this._firstRun.showRoomDevices = showDevices.checked === true;
    this._render();
  });

  const actions = el("div", "actions");
  const back = el("button", "secondary", "Назад к началу");
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
    card.appendChild(el(
      "div",
      "muted action-help",
      "Кнопка станет доступна после успешной проверки хотя бы одной комнаты.",
    ));
  }
  this._firstRunFields = fields;
}
