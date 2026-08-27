import { dupAttention, dupCompare, dupFilter, dupGroups, dupGuide, dupSize, dupView } from "./hausman-hub-inventory-duplicates.js?v=1.52.186";
import { withCorrelationId } from "./hausman-hub-correlation.js?v=1.52.186";
import { propertyNamesSection } from "./hausman-hub-device-property-names.js?v=1.52.186";

const DEVICE_MAINTENANCE_API = "hausman_hub/v1/admin/device-maintenance";
const Z2M_DEVICE_IMAGE =
  /^https:\/\/www\.zigbee2mqtt\.io\/images\/devices\/(?:[A-Za-z0-9._~-]|%[0-9A-F]{2})+\.png$/;
const INVENTORY_PAGE_SIZE = 16;

const INVENTORY_FILTERS = [
  { id: "attention", label: "Требуют внимания" },
  { id: "all", label: "Все" },
  { id: "unassigned", label: "Без комнаты" },
  { id: "unavailable", label: "Нет связи" },
  { id: "virtual", label: "Виртуальные" },
  { id: "entity_only", label: "Отдельные сущности" },
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

function metric(el, value, label, tone = "") {
  const item = el("div", `device-inventory-metric${tone ? ` ${tone}` : ""}`);
  item.appendChild(el("strong", null, String(value ?? 0)));
  item.appendChild(el("span", null, label));
  return item;
}

function stateFor(panel) {
  if (!panel._deviceMaintenance) {
    panel._deviceMaintenance = {
      data: null, loading: false, error: "", action: "", message: "", messageTone: "", confirmDelete: null,
    };
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
    const payload = await panel._hass.callApi("GET", DEVICE_MAINTENANCE_API);
    const rawDevices = payload && payload.devices;
    const devices = Array.isArray(rawDevices)
      ? rawDevices
      : Object.entries(rawDevices || {}).map(([id, item]) => ({
        ...item, id, areaId: item.areaId ?? item.roomAreaId ?? null,
      }));
    state.data = {
      ...payload,
      devicesById: Object.fromEntries(devices.map((item) => [item.id, item])),
    };
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
  state.messageTone = "";
  repaint();
  try {
    await panel._hass.callApi("POST", DEVICE_MAINTENANCE_API, withCorrelationId(DEVICE_MAINTENANCE_API, {
      contract: { name: "hausman-hub-device-maintenance-request", version: 1 },
      expectedRevision: state.data.snapshotRevision,
      action, deviceId: device.id, ...payload,
    }));
    state.message = action === "update"
      ? "Название и комната сохранены в Home Assistant."
      : action === "identify"
        ? "Команда поиска отправлена устройству."
        : "Запись удалена из реестра Home Assistant.";
    state.messageTone = "is-success";
    state.confirmDelete = null;
    state.data = null;
    if (action === "delete") panel._inventoryDeviceId = null;
    await loadMaintenance(panel, repaint, true);
    if (typeof panel._load === "function") await panel._load();
  } catch (error) {
    state.message = error && error.status === 409
      ? action === "delete"
        ? "Удаление заблокировано: найдена зависимость или проверка ссылок неполна."
        : "Список устройств изменился. Обновите его и повторите действие."
      : "Операция не выполнена. Реестр Home Assistant не изменён.";
    state.messageTone = "is-error";
  } finally {
    state.action = "";
    if (typeof panel._render === "function") panel._render();
    else repaint();
  }
}

function detailPanel(panel, el, device, maintenance, repaint, duplicateGroupSize = 1) {
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
  if (!maintenance) {
    detail.appendChild(el("p", "muted", "Запись уже удалена или изменилась. Обновите инвентаризацию."));
    return detail;
  }

  dupGuide(detail, el, device, duplicateGroupSize);

  const overview = el("div", "device-maintenance-overview");
  const usage = el("section", "device-maintenance-usage");
  usage.appendChild(el("h4", null, "Где используется"));
  const uses = Array.isArray(maintenance.uses) ? maintenance.uses : [];
  if (uses.length) {
    uses.forEach((item) => {
      const row = el("div", "device-maintenance-use");
      row.appendChild(el("strong", null, item.name || "Hausman Hub"));
      row.appendChild(el("small", "muted", "Эта ссылка должна быть удалена до удаления устройства."));
      usage.appendChild(row);
    });
  } else {
    usage.appendChild(el("strong", "device-maintenance-free", maintenance.deleteEligible === false
      ? "Удаление временно заблокировано"
      : "Не используется настройками Hausman Hub"));
    const warnings = state.data && state.data.usageIndex && Array.isArray(state.data.usageIndex.warnings)
      ? state.data.usageIndex.warnings : [];
    usage.appendChild(el("p", "muted", warnings[0] || "Hausman Hub проверил свои настройки, автоматизации, сценарии, группы и скрипты Home Assistant."));
  }
  overview.appendChild(usage);

  const composition = el("section", "device-maintenance-entities");
  composition.appendChild(el("h4", null, `Возможности устройства · ${maintenance.entityCount || 0}`));
  const entities = Array.isArray(maintenance.entities) ? maintenance.entities : [];
  if (entities.length) {
    const entityList = el("details", "device-maintenance-entity-list");
    entityList.appendChild(el("summary", null, "Показать состав"));
    const rows = el("div", "device-maintenance-entity-rows");
    entities.forEach((item) => {
      const entity = el("div", "device-maintenance-entity-row");
      entity.appendChild(el("strong", null, item.name || "Сущность"));
      entity.appendChild(el("code", "device-maintenance-entity-id", item.id || "Идентификатор не указан"));
      entity.appendChild(el("small", "muted", item.disabled ? "Отключена в Home Assistant" : "Доступна в Home Assistant"));
      rows.appendChild(entity);
    });
    entityList.appendChild(rows);
    composition.appendChild(entityList);

    composition.appendChild(propertyNamesSection({
      panel, el, state, repaint,
      reload: () => loadMaintenance(panel, repaint, true),
    }, entities));
  } else {
    composition.appendChild(el("p", "muted", "Сущностей нет. Это может быть устаревшая запись реестра."));
  }
  overview.appendChild(composition);
  detail.appendChild(overview);

  if (device.kind === "entity_only") {
    const note = el("div", "device-maintenance-entity-note");
    note.appendChild(el("strong", null, "Отдельная сущность Home Assistant"));
    note.appendChild(el("span", null, "У неё нет физической карточки устройства. Название и комната сохраняются прямо в реестре сущностей Home Assistant."));
    detail.appendChild(note);
  }

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
    option.selected = area.id === maintenance.areaId;
    room.appendChild(option);
  });
  roomLabel.appendChild(room);
  form.appendChild(roomLabel);
  const save = el("button", "primary", state.action === "update" ? "Сохраняем…" : "Сохранить имя и комнату");
  save.type = "button";
  save.disabled = Boolean(state.action);
  save.addEventListener("click", () => runAction(panel, device, "update", {
    changes: { name: name.value, areaId: room.value || null },
  }, repaint));
  form.appendChild(save);
  detail.appendChild(form);

  const actions = el("div", "device-maintenance-actions");
  const open = el("a", "button secondary", "Открыть в Home Assistant");
  open.href = maintenance.haUrl;
  open.title = "Открыть исходную карточку, сущности и автоматизации устройства";
  actions.appendChild(open);
  const identify = el("button", "secondary", maintenance.identifySupported ? "Найти устройство" : "Поиск не поддерживается");
  identify.type = "button";
  identify.disabled = !maintenance.identifySupported || Boolean(state.action);
  identify.title = maintenance.identifySupported
    ? `Home Assistant вызовет ${maintenance.identifyLabel || "команду идентификации"}.`
    : "Устройство не предоставляет команду Identify/Locate, поэтому фиктивный поиск не показывается.";
  identify.addEventListener("click", () => runAction(panel, device, "identify", { confirmed: true }, repaint));
  actions.appendChild(identify);
  const remove = el("button", "secondary danger-outline", "Удалить из Home Assistant");
  remove.type = "button";
  remove.disabled = maintenance.deleteBlocked || Boolean(state.action);
  remove.title = maintenance.deleteBlocked
    ? `Сначала уберите использование: ${(maintenance.deleteBlockers || []).join(", ")}.`
    : "Подключённая интеграция может создать устройство снова.";
  remove.addEventListener("click", () => {
    state.confirmDelete = device.id;
    state.message = "";
    repaint();
  });
  actions.appendChild(remove);
  detail.appendChild(actions);
  if (state.confirmDelete === device.id) {
    const confirmation = el("div", "device-maintenance-confirm");
    confirmation.role = "alertdialog";
    confirmation["aria-label"] = "Подтверждение удаления записи Home Assistant";
    const confirmationCopy = el("div");
    confirmationCopy.appendChild(el("strong", null, "Удалить запись из Home Assistant?"));
    confirmationCopy.appendChild(el("p", "muted", "Это не отключает физическое устройство. Интеграция может создать запись снова."));
    confirmation.appendChild(confirmationCopy);
    const confirmationActions = el("div", "device-maintenance-confirm-actions");
    const cancel = el("button", "secondary", "Отмена");
    cancel.type = "button";
    cancel.addEventListener("click", () => {
      state.confirmDelete = null;
      repaint();
    });
    confirmationActions.appendChild(cancel);
    const confirm = el("button", "danger", "Удалить запись");
    confirm.type = "button";
    confirm.addEventListener("click", () => runAction(panel, device, "delete", { confirmed: true }, repaint));
    confirmationActions.appendChild(confirm);
    confirmation.appendChild(confirmationActions);
    detail.appendChild(confirmation);
  }
  if (state.message) {
    const message = el("div", `device-maintenance-message ${state.messageTone || ""}`, state.message);
    message.role = state.messageTone === "is-error" ? "alert" : "status";
    message["aria-live"] = state.messageTone === "is-error" ? "assertive" : "polite";
    detail.appendChild(message);
  }
  return detail;
}

function inventoryRow(panel, el, device, repaint, duplicateGroupSize = 1, duplicateGroupPosition = 1) {
  const expanded = panel._inventoryDeviceId === device.id;
  const row = el("article", `device-inventory-row${dupAttention(device, duplicateGroupSize) ? " needs-attention" : ""}${expanded ? " is-expanded" : ""}${duplicateGroupSize > 1 ? " is-duplicate-group" : ""}`);
  const summary = el("button", "device-inventory-summary");
  summary.type = "button";
  if (typeof summary.setAttribute === "function") {
    summary.setAttribute("aria-expanded", expanded ? "true" : "false");
  } else {
    summary.ariaExpanded = expanded ? "true" : "false";
  }
  const identity = el("div", "device-inventory-identity");
  const visual = el("span", "device-inventory-visual");
  if (Z2M_DEVICE_IMAGE.test(String(device.imageUrl || ""))) {
    const image = el("img");
    image.src = device.imageUrl;
    image.alt = "";
    image.loading = "lazy";
    visual.appendChild(image);
  } else {
    visual.appendChild(el("span", "device-inventory-neutral", "◇"));
  }
  identity.appendChild(visual);
  const identityCopy = el("span", "device-inventory-identity-copy");
  identityCopy.appendChild(el("strong", null, device.name || "Устройство без названия"));
  identityCopy.appendChild(el("small", "muted", [device.manufacturer, device.model].filter(Boolean).join(" · ") || KIND_LABELS[device.kind] || "Устройство Home Assistant"));
  identity.appendChild(identityCopy);
  summary.appendChild(identity);
  const room = el("div", "device-inventory-room");
  room.appendChild(el("span", "device-inventory-label", "Комната"));
  room.appendChild(el("strong", null, device.roomName || "Не привязано"));
  summary.appendChild(room);
  const composition = el("div", "device-inventory-composition");
  composition.appendChild(el("span", "device-inventory-label", KIND_LABELS[device.kind] || "Устройство"));
  const count = Number(device.entityCount) || 0;
  const capabilityLabel = count === 1 ? "возможность" : count > 1 && count < 5 ? "возможности" : "возможностей";
  composition.appendChild(el("strong", null, `${count} ${capabilityLabel}`));
  summary.appendChild(composition);
  const status = el("div", `device-inventory-status is-${device.status || "available"}`);
  const duplicate = dupView(device, duplicateGroupSize, duplicateGroupPosition);
  if (duplicate) {
    status.className += ` ${duplicate.className}`;
    status.appendChild(el("strong", null, duplicate.title));
    status.appendChild(el("small", null, duplicate.detail));
  } else {
    status.appendChild(el("strong", null, STATUS_LABELS[device.status] || "Состояние неизвестно"));
    if (device.reason) status.appendChild(el("small", null, device.reason));
  }
  summary.appendChild(status);
  summary.appendChild(el("span", "device-inventory-chevron", expanded ? "⌃" : "⌄"));
  summary.addEventListener("click", () => {
    panel._inventoryDeviceId = expanded ? null : device.id;
    repaint();
    if (!expanded) loadMaintenance(panel, repaint);
  });
  row.appendChild(summary);
  if (expanded) {
    const maintenance = stateFor(panel).data && stateFor(panel).data.devicesById
      ? stateFor(panel).data.devicesById[device.id] : null;
    row.appendChild(detailPanel(panel, el, device, maintenance, repaint, duplicateGroupSize));
  }
  return row;
}

export function renderDeviceInventory(panel, container, helpers) {
  const { el, normalizedText } = helpers;
  const inventory = panel._homeDashboard && panel._homeDashboard.inventory;
  const devices = inventory && Array.isArray(inventory.devices) ? inventory.devices : [];
  if (!devices.length) return;
  const duplicateGroups = dupGroups(devices);
  const summary = inventory.summary || {};
  const section = el("section", "card device-inventory");
  const heading = el("div", "device-inventory-heading-row");
  const copy = el("div", "device-inventory-heading");
  copy.appendChild(el("div", "settings-heading-eyebrow", "Инвентаризация и обслуживание"));
  copy.appendChild(el("h3", null, "Устройства Home Assistant"));
  copy.appendChild(el("p", "muted", "Одна строка соответствует одной физической записи. Откройте её, чтобы увидеть использование, изменить имя или комнату, найти устройство и безопасно удалить устаревшую запись."));
  heading.appendChild(copy);
  const refresh = el("button", "secondary device-inventory-refresh", "Обновить список");
  refresh.type = "button";
  refresh.disabled = stateFor(panel).loading;
  refresh.addEventListener("click", async () => {
    const state = stateFor(panel);
    state.data = null;
    state.message = "";
    await loadMaintenance(panel, () => {
      if (typeof panel._render === "function") panel._render();
    }, true);
    if (typeof panel._load === "function") await panel._load();
  });
  heading.appendChild(refresh);
  section.appendChild(heading);
  const metrics = el("div", "device-inventory-metrics");
  metrics.appendChild(metric(el, summary.physicalDeviceCount ?? summary.canonicalDeviceCount, "физических устройств"));
  metrics.appendChild(metric(el, summary.logicalEntityCount, "отдельных сущностей"));
  metrics.appendChild(metric(el, summary.unassignedCount, "без комнаты", summary.unassignedCount ? "is-warning" : ""));
  metrics.appendChild(metric(el, summary.unavailableCount, "нет связи", summary.unavailableCount ? "is-warning" : ""));
  metrics.appendChild(metric(el, summary.duplicateGroupCount, "групп дублей", summary.duplicateGroupCount ? "is-warning" : ""));
  section.appendChild(metrics);
  const toolbar = el("div", "device-inventory-toolbar");
  const filters = el("div", "device-inventory-filters");
  const defaultFilter = Number(summary.attentionCount) > 0 ? "attention" : "all";
  let activeFilter = panel._inventoryFilter || defaultFilter;
  const list = el("div", "device-inventory-list");
  const filterNote = el("div", "device-inventory-filter-note");
  filterNote.appendChild(el("strong", null, "Как выбрать запись"));
  filterNote.appendChild(el("span", null, "Оставьте рекомендуемую основную запись. Копию удаляйте только после проверки разделов «Где используется» и «Возможности устройства»."));
  const search = el("input", "device-inventory-search");
  const renderRows = () => {
    list.innerHTML = "";
    filterNote.hidden = activeFilter !== "duplicates";
    const query = normalizedText(search.value || "");
    panel._inventoryQuery = search.value || "";
    const matching = devices.filter((device) => dupFilter(
      device, activeFilter, dupSize(duplicateGroups, device),
    ) && (!query || normalizedText([
      device.name, device.roomName, device.manufacturer, device.model, ...(device.domains || []),
    ].filter(Boolean).join(" ")).includes(query))).sort(dupCompare);
    const limit = Number(panel._inventoryLimit) || INVENTORY_PAGE_SIZE;
    const visible = matching.slice(0, limit);
    const groupPositions = new Map();
    visible.forEach((device) => {
      const groupKey = device.canonicalId || device.id;
      const position = (groupPositions.get(groupKey) || 0) + 1;
      groupPositions.set(groupKey, position);
      list.appendChild(inventoryRow(
        panel, el, device, renderRows, duplicateGroups.get(groupKey) || 1, position,
      ));
    });
    if (!visible.length) {
      const empty = el("div", "device-inventory-empty");
      empty.appendChild(el("strong", null, "Ничего не найдено"));
      empty.appendChild(el("p", "muted", "Измените фильтр или поисковый запрос."));
      list.appendChild(empty);
    }
    if (matching.length > visible.length) {
      const more = el("button", "secondary device-inventory-more", `Показать ещё ${Math.min(INVENTORY_PAGE_SIZE, matching.length - visible.length)}`);
      more.type = "button";
      more.addEventListener("click", () => {
        panel._inventoryLimit = limit + INVENTORY_PAGE_SIZE;
        renderRows();
      });
      list.appendChild(more);
    }
  };
  INVENTORY_FILTERS.forEach((filter) => {
    const button = el("button", filter.id === activeFilter ? "is-current" : "", filter.label);
    button.type = "button";
    button.addEventListener("click", () => {
      panel._inventoryFilter = filter.id;
      panel._inventoryLimit = INVENTORY_PAGE_SIZE;
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
  search.addEventListener("input", () => {
    panel._inventoryLimit = INVENTORY_PAGE_SIZE;
    renderRows();
  });
  toolbar.appendChild(search);
  section.appendChild(toolbar);
  section.appendChild(filterNote);
  section.appendChild(list);
  renderRows();
  container.appendChild(section);
}
