const DEVICE_MAINTENANCE_API = "hausman_hub/v1/admin/device-maintenance";

const INVENTORY_FILTERS = [
  { id: "attention", label: "Требуют внимания" },
  { id: "all", label: "Все" },
  { id: "unassigned", label: "Без комнаты" },
  { id: "unavailable", label: "Нет связи" },
  { id: "virtual", label: "Виртуальные" },
  { id: "duplicates", label: "Возможные дубли" },
];

const KIND_LABELS = {
  physical: "Физическое устройство",
  virtual: "Виртуальный контур",
  entity_only: "Отдельная сущность",
};

const STATUS_LABELS = {
  available: "Доступно",
  unavailable: "Нет связи",
  empty: "Без сущностей",
  disabled: "Отключено",
};

function isAttention(device) {
  return device.possibleDuplicate === true || !device.roomId || device.status !== "available";
}

function matchesFilter(device, filter) {
  if (filter === "all") return true;
  if (filter === "attention") return isAttention(device);
  if (filter === "unassigned") return !device.roomId;
  if (filter === "unavailable") return device.status === "unavailable";
  if (filter === "virtual") return device.kind === "virtual";
  if (filter === "duplicates") return device.possibleDuplicate === true;
  return true;
}

function metric(el, value, label, tone = "") {
  const item = el("div", `device-inventory-metric${tone ? ` ${tone}` : ""}`);
  item.appendChild(el("strong", null, String(value ?? 0)));
  item.appendChild(el("span", null, label));
  return item;
}

function stateFor(panel) {
  if (!panel._deviceMaintenance) {
    panel._deviceMaintenance = { data: null, loading: false, error: "", action: "", message: "" };
  }
  return panel._deviceMaintenance;
}

async function loadMaintenance(panel, repaint, force = false) {
  const state = stateFor(panel);
  if (!panel._hass || state.loading || (state.data && !force)) return;
  state.loading = true;
  state.error = "";
  repaint();
  try {
    state.data = await panel._hass.callApi("GET", DEVICE_MAINTENANCE_API);
  } catch (error) {
    state.error = "Не удалось получить права обслуживания реестра Home Assistant.";
  } finally {
    state.loading = false;
    repaint();
  }
}

async function runAction(panel, device, action, payload, repaint) {
  const state = stateFor(panel);
  if (!panel._hass || state.action) return;
  state.action = action;
  state.message = "";
  repaint();
  try {
    await panel._hass.callApi("POST", DEVICE_MAINTENANCE_API, {
      action, deviceId: device.id, ...payload,
    });
    state.message = action === "update"
      ? "Название и комната сохранены в Home Assistant."
      : action === "identify"
        ? "Команда поиска отправлена устройству."
        : "Запись удалена из реестра Home Assistant.";
    state.data = null;
    if (action === "delete") panel._inventoryDeviceId = null;
    await loadMaintenance(panel, repaint, true);
    if (typeof panel._load === "function") await panel._load();
  } catch (error) {
    state.message = error && error.status === 409
      ? "Удаление заблокировано: устройство ещё используется HausmanHub."
      : "Операция не выполнена. Реестр Home Assistant не изменён.";
  } finally {
    state.action = "";
    if (typeof panel._render === "function") panel._render();
    else repaint();
  }
}

function detailPanel(panel, el, device, maintenance, repaint) {
  const detail = el("div", "device-maintenance-detail");
  const state = stateFor(panel);
  if (state.loading) {
    detail.appendChild(el("p", "muted", "Проверяем реестр и зависимости…"));
    return detail;
  }
  if (state.error) {
    detail.appendChild(el("strong", "device-maintenance-error", state.error));
    const retry = el("button", null, "Повторить");
    retry.type = "button";
    retry.addEventListener("click", () => loadMaintenance(panel, repaint, true));
    detail.appendChild(retry);
    return detail;
  }
  if (device.kind === "entity_only") {
    detail.appendChild(el("strong", null, "Это отдельная сущность, а не устройство"));
    detail.appendChild(el("p", "muted", "Переименование и комната такой сущности изменяются в её карточке Home Assistant. HausmanHub не создаёт фиктивную запись устройства."));
    return detail;
  }
  if (!maintenance) {
    detail.appendChild(el("p", "muted", "Запись уже удалена или изменилась. Обновите инвентаризацию."));
    return detail;
  }

  const overview = el("div", "device-maintenance-overview");
  const usage = el("section", "device-maintenance-usage");
  usage.appendChild(el("h4", null, "Где используется"));
  const uses = Array.isArray(maintenance.uses) ? maintenance.uses : [];
  if (uses.length) {
    uses.forEach((item) => {
      const row = el("div", "device-maintenance-use");
      row.appendChild(el("strong", null, item.title || "HausmanHub"));
      row.appendChild(el("small", "muted", item.detail || "Устройство используется."));
      usage.appendChild(row);
    });
  } else {
    usage.appendChild(el("strong", "device-maintenance-free", "Не используется настройками HausmanHub"));
    usage.appendChild(el("p", "muted", "Автоматизации Home Assistant могут обращаться к отдельным сущностям — перед удалением проверьте их в исходной карточке."));
  }
  overview.appendChild(usage);

  const composition = el("section", "device-maintenance-entities");
  composition.appendChild(el("h4", null, `Состав · ${maintenance.entityCount || 0}`));
  const entities = Array.isArray(maintenance.entities) ? maintenance.entities : [];
  if (entities.length) {
    const names = entities.slice(0, 6).map((item) => item.name || item.id).join(" · ");
    composition.appendChild(el("p", "muted", names));
    if (entities.length > 6) composition.appendChild(el("small", "muted", `Ещё ${entities.length - 6}`));
  } else {
    composition.appendChild(el("p", "muted", "Сущностей нет. Это может быть устаревшая запись реестра."));
  }
  overview.appendChild(composition);
  detail.appendChild(overview);

  const form = el("div", "device-maintenance-form");
  const nameLabel = el("label", null);
  nameLabel.appendChild(el("span", null, "Название устройства"));
  const name = el("input");
  name.value = maintenance.name || device.name || "";
  name.maxLength = 128;
  nameLabel.appendChild(name);
  form.appendChild(nameLabel);
  const roomLabel = el("label", null);
  roomLabel.appendChild(el("span", null, "Комната Home Assistant"));
  const room = el("select");
  const none = el("option", null, "Без комнаты");
  none.value = "";
  room.appendChild(none);
  const areas = state.data && Array.isArray(state.data.areas) ? state.data.areas : [];
  areas.forEach((area) => {
    const option = el("option", null, area.name);
    option.value = area.id;
    option.selected = area.id === maintenance.roomAreaId;
    room.appendChild(option);
  });
  roomLabel.appendChild(room);
  form.appendChild(roomLabel);
  const save = el("button", "primary", state.action === "update" ? "Сохраняем…" : "Сохранить в Home Assistant");
  save.type = "button";
  save.disabled = Boolean(state.action);
  save.addEventListener("click", () => runAction(panel, device, "update", {
    name: name.value, areaId: room.value || null,
  }, repaint));
  form.appendChild(save);
  detail.appendChild(form);

  const actions = el("div", "device-maintenance-actions");
  const open = el("a", "button secondary", "Открыть в Home Assistant");
  open.href = maintenance.haUrl;
  open.title = "Открыть исходную карточку, сущности и автоматизации устройства";
  actions.appendChild(open);
  const identify = el("button", null, maintenance.identifySupported ? "Найти устройство" : "Поиск не поддерживается");
  identify.type = "button";
  identify.disabled = !maintenance.identifySupported || Boolean(state.action);
  identify.title = maintenance.identifySupported
    ? `Home Assistant вызовет ${maintenance.identifyLabel || "команду идентификации"}.`
    : "Устройство не предоставляет команду Identify/Locate, поэтому фиктивный поиск не показывается.";
  identify.addEventListener("click", () => runAction(panel, device, "identify", {}, repaint));
  actions.appendChild(identify);
  const remove = el("button", "danger subtle", "Удалить из реестра");
  remove.type = "button";
  remove.disabled = maintenance.deleteBlocked || Boolean(state.action);
  remove.title = maintenance.deleteBlocked
    ? `Сначала уберите использование: ${(maintenance.deleteBlockers || []).join(", ")}.`
    : "Подключённая интеграция может создать устройство снова.";
  remove.addEventListener("click", () => {
    const warning = `Удалить «${device.name}» из реестра Home Assistant? Подключённая интеграция может создать его снова.`;
    if (window.confirm(warning)) runAction(panel, device, "delete", { confirmed: true }, repaint);
  });
  actions.appendChild(remove);
  detail.appendChild(actions);
  if (state.message) detail.appendChild(el("div", "device-maintenance-message", state.message));
  return detail;
}

function inventoryRow(panel, el, device, repaint) {
  const expanded = panel._inventoryDeviceId === device.id;
  const row = el("article", `device-inventory-row${isAttention(device) ? " needs-attention" : ""}${expanded ? " is-expanded" : ""}`);
  const summary = el("button", "device-inventory-summary");
  summary.type = "button";
  if (typeof summary.setAttribute === "function") {
    summary.setAttribute("aria-expanded", expanded ? "true" : "false");
  } else {
    summary.ariaExpanded = expanded ? "true" : "false";
  }
  const identity = el("div", "device-inventory-identity");
  identity.appendChild(el("strong", null, device.name || "Устройство без названия"));
  identity.appendChild(el("small", "muted", [device.manufacturer, device.model].filter(Boolean).join(" · ") || KIND_LABELS[device.kind] || "Устройство Home Assistant"));
  summary.appendChild(identity);
  const room = el("div", "device-inventory-room");
  room.appendChild(el("span", "device-inventory-label", "Комната"));
  room.appendChild(el("strong", null, device.roomName || "Не привязано"));
  summary.appendChild(room);
  const composition = el("div", "device-inventory-composition");
  composition.appendChild(el("span", "device-inventory-label", KIND_LABELS[device.kind] || "Устройство"));
  const count = Number(device.entityCount) || 0;
  composition.appendChild(el("strong", null, `${count} ${count === 1 ? "сущность" : "сущности"}`));
  summary.appendChild(composition);
  const status = el("div", `device-inventory-status is-${device.status || "available"}`);
  status.appendChild(el("strong", null, device.possibleDuplicate ? "Возможный дубль" : STATUS_LABELS[device.status] || "Состояние неизвестно"));
  if (device.reason) status.appendChild(el("small", null, device.reason));
  summary.appendChild(status);
  summary.appendChild(el("span", "device-inventory-chevron", expanded ? "⌃" : "⌄"));
  summary.addEventListener("click", () => {
    panel._inventoryDeviceId = expanded ? null : device.id;
    repaint();
    if (!expanded && device.kind !== "entity_only") loadMaintenance(panel, repaint);
  });
  row.appendChild(summary);
  if (expanded) {
    const maintenance = stateFor(panel).data && stateFor(panel).data.devices
      ? stateFor(panel).data.devices[device.id] : null;
    row.appendChild(detailPanel(panel, el, device, maintenance, repaint));
  }
  return row;
}

export function renderDeviceInventory(panel, container, helpers) {
  const { el, normalizedText } = helpers;
  const inventory = panel._homeDashboard && panel._homeDashboard.inventory;
  const devices = inventory && Array.isArray(inventory.devices) ? inventory.devices : [];
  if (!devices.length) return;
  const summary = inventory.summary || {};
  const section = el("section", "card device-inventory");
  const copy = el("div", "device-inventory-heading");
  copy.appendChild(el("div", "settings-heading-eyebrow", "Инвентаризация и обслуживание"));
  copy.appendChild(el("h3", null, "Что Home Assistant считает устройствами"));
  copy.appendChild(el("p", "muted", "Откройте карточку, чтобы проверить использование, переименовать устройство, назначить комнату, найти его физически или безопасно удалить запись."));
  section.appendChild(copy);
  const metrics = el("div", "device-inventory-metrics");
  metrics.appendChild(metric(el, summary.canonicalDeviceCount, "основных устройств"));
  metrics.appendChild(metric(el, summary.unassignedCount, "без комнаты", summary.unassignedCount ? "is-warning" : ""));
  metrics.appendChild(metric(el, summary.unavailableCount, "нет связи", summary.unavailableCount ? "is-warning" : ""));
  metrics.appendChild(metric(el, summary.duplicateGroupCount, "групп дублей", summary.duplicateGroupCount ? "is-warning" : ""));
  section.appendChild(metrics);
  const toolbar = el("div", "device-inventory-toolbar");
  const filters = el("div", "device-inventory-filters");
  const defaultFilter = Number(summary.attentionCount) > 0 ? "attention" : "all";
  let activeFilter = panel._inventoryFilter || defaultFilter;
  const list = el("div", "device-inventory-list");
  const search = el("input", "device-inventory-search");
  const renderRows = () => {
    list.innerHTML = "";
    const query = normalizedText(search.value || "");
    panel._inventoryQuery = search.value || "";
    const visible = devices.filter((device) => matchesFilter(device, activeFilter) && (!query || normalizedText([
      device.name, device.roomName, device.manufacturer, device.model, ...(device.domains || []),
    ].filter(Boolean).join(" ")).includes(query)));
    visible.forEach((device) => list.appendChild(inventoryRow(panel, el, device, renderRows)));
    if (!visible.length) {
      const empty = el("div", "device-inventory-empty");
      empty.appendChild(el("strong", null, "Ничего не найдено"));
      empty.appendChild(el("p", "muted", "Измените фильтр или поисковый запрос."));
      list.appendChild(empty);
    }
  };
  INVENTORY_FILTERS.forEach((filter) => {
    const button = el("button", filter.id === activeFilter ? "is-current" : "", filter.label);
    button.type = "button";
    button.addEventListener("click", () => {
      panel._inventoryFilter = filter.id;
      activeFilter = filter.id;
      Array.from(filters.children).forEach((child, index) => {
        child.className = INVENTORY_FILTERS[index].id === activeFilter ? "is-current" : "";
      });
      renderRows();
    });
    filters.appendChild(button);
  });
  toolbar.appendChild(filters);
  search.type = "search";
  search.placeholder = "Найти устройство, комнату или тип";
  search.value = panel._inventoryQuery || "";
  search.addEventListener("input", renderRows);
  toolbar.appendChild(search);
  section.appendChild(toolbar);
  section.appendChild(list);
  renderRows();
  container.appendChild(section);
}
