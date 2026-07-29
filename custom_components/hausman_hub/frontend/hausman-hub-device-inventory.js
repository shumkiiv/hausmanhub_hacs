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
  return device.possibleDuplicate === true
    || !device.roomId
    || device.status !== "available";
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

function inventoryRow(el, device) {
  const row = el(
    "article",
    `device-inventory-row${isAttention(device) ? " needs-attention" : ""}`,
  );
  const identity = el("div", "device-inventory-identity");
  identity.appendChild(el("strong", null, device.name || "Устройство без названия"));
  identity.appendChild(el(
    "small",
    "muted",
    [device.manufacturer, device.model].filter(Boolean).join(" · ")
      || KIND_LABELS[device.kind]
      || "Устройство Home Assistant",
  ));
  row.appendChild(identity);

  const room = el("div", "device-inventory-room");
  room.appendChild(el("span", "device-inventory-label", "Комната"));
  room.appendChild(el("strong", null, device.roomName || "Не привязано"));
  row.appendChild(room);

  const composition = el("div", "device-inventory-composition");
  composition.appendChild(el("span", "device-inventory-label", KIND_LABELS[device.kind] || "Устройство"));
  const entityCount = Number(device.entityCount) || 0;
  composition.appendChild(el(
    "strong",
    null,
    `${entityCount} ${entityCount === 1 ? "сущность" : "сущности"}`,
  ));
  row.appendChild(composition);

  const status = el("div", `device-inventory-status is-${device.status || "available"}`);
  status.appendChild(el("strong", null, device.possibleDuplicate
    ? "Возможный дубль"
    : STATUS_LABELS[device.status] || "Состояние неизвестно"));
  if (device.reason) status.appendChild(el("small", null, device.reason));
  row.appendChild(status);
  return row;
}

export function renderDeviceInventory(panel, container, helpers) {
  const { el, normalizedText } = helpers;
  const inventory = panel._homeDashboard && panel._homeDashboard.inventory;
  const devices = inventory && Array.isArray(inventory.devices)
    ? inventory.devices : [];
  if (!devices.length) return;

  const summary = inventory.summary || {};
  const section = el("section", "card device-inventory");
  const heading = el("div", "device-inventory-heading");
  const copy = el("div");
  copy.appendChild(el("div", "settings-heading-eyebrow", "Инвентаризация"));
  copy.appendChild(el("h3", null, "Что Home Assistant считает устройствами"));
  copy.appendChild(el(
    "p",
    "muted",
    "Одна основная карточка соответствует одному реальному устройству. Виртуальные контуры и вероятные дубли остаются видимыми здесь для проверки, но не размножаются на экранах управления.",
  ));
  heading.appendChild(copy);
  section.appendChild(heading);

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
  INVENTORY_FILTERS.forEach((filter) => {
    const button = el("button", filter.id === activeFilter ? "is-current" : "", filter.label);
    button.type = "button";
    button.addEventListener("click", () => {
      panel._inventoryFilter = filter.id;
      activeFilter = filter.id;
      renderRows();
      Array.from(filters.children).forEach((child, index) => {
        child.className = INVENTORY_FILTERS[index].id === activeFilter ? "is-current" : "";
      });
    });
    filters.appendChild(button);
  });
  toolbar.appendChild(filters);
  const search = el("input", "device-inventory-search");
  search.type = "search";
  search.placeholder = "Найти устройство";
  search.value = panel._inventoryQuery || "";
  toolbar.appendChild(search);
  section.appendChild(toolbar);

  const list = el("div", "device-inventory-list");
  section.appendChild(list);
  const renderRows = () => {
    list.innerHTML = "";
    const query = normalizedText(search.value || "");
    panel._inventoryQuery = search.value || "";
    const visible = devices.filter((device) => {
      if (!matchesFilter(device, activeFilter)) return false;
      if (!query) return true;
      return normalizedText([
        device.name,
        device.roomName,
        device.manufacturer,
        device.model,
        ...(device.domains || []),
      ].filter(Boolean).join(" ")).includes(query);
    });
    visible.forEach((device) => list.appendChild(inventoryRow(el, device)));
    if (!visible.length) {
      const empty = el("div", "device-inventory-empty");
      empty.appendChild(el("strong", null, "Ничего не найдено"));
      empty.appendChild(el("p", "muted", "Измените фильтр или поисковый запрос."));
      list.appendChild(empty);
    }
  };
  search.addEventListener("input", renderRows);
  renderRows();
  container.appendChild(section);
}
