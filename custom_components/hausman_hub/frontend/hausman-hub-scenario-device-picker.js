/* Physical-device picker and HA state helpers for the scenario editor. */

import { scenarioField, scenarioSelectField } from "./hausman-hub-scenario-fields.js?v=1.52.164";

const COMPARISONS = [
  ["equals", "равно"], ["not_equals", "не равно"], ["above", "выше"],
  ["below", "ниже"], ["changed", "изменилось"],
];

export function scenarioPhysicalGroups(devices, actionsOnly = false) {
  const groups = new Map();
  devices.forEach((device) => {
    if (actionsOnly && !(Array.isArray(device.actions) && device.actions.length)) return;
    const key = device.physical_id || device.target_id;
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        name: device.physical_name || device.name || "Устройство",
        roomName: device.room_name || "Без комнаты",
        typeName: device.device_type_name || "Устройства",
        entries: [],
      });
    }
    groups.get(key).entries.push(device);
  });
  return Array.from(groups.values()).sort((left, right) =>
    `${left.roomName}|${left.typeName}|${left.name}`.localeCompare(`${right.roomName}|${right.typeName}|${right.name}`, "ru"));
}

export function scenarioGroupForTarget(groups, targetId) {
  return groups.find((group) => group.entries.some((entry) => entry.target_id === targetId));
}

function preferredEntry(group, actionsOnly = false) {
  const entries = group && group.entries || [];
  if (actionsOnly) return entries.find((entry) => Array.isArray(entry.actions) && entry.actions.length) || entries[0];
  return entries.find((entry) => String(entry.entity_id || "").startsWith("binary_sensor."))
    || entries.find((entry) => ["light", "switch", "climate", "cover", "lock"].includes(String(entry.entity_id || "").split(".")[0]))
    || entries[0];
}

export function scenarioCapabilityLabel(device) {
  return device && (device.capability_name || device.name || device.entity_id || device.target_id) || "Возможность устройства";
}

function openPicker(panel, deps, devices, selectedId, actionsOnly, onSelected) {
  const groups = scenarioPhysicalGroups(devices, actionsOnly);
  let selectedRoom = "";
  let selectedType = "";
  const overlay = deps.el("div", "scenario-device-picker-overlay");
  deps.setAttr(overlay, "role", "dialog");
  deps.setAttr(overlay, "aria-modal", "true");
  deps.setAttr(overlay, "aria-label", "Выбор устройства");
  const dialog = deps.el("section", "scenario-device-picker-dialog");
  const head = deps.el("header", "scenario-device-picker-head");
  const copy = deps.el("div");
  copy.appendChild(deps.el("span", "scenario-editor-kicker", "УСТРОЙСТВА HOME ASSISTANT"));
  copy.appendChild(deps.el("h3", null, "Выберите физическое устройство"));
  copy.appendChild(deps.el("p", null, "Один прибор показан один раз. Комната и тип помогут быстро его найти."));
  head.appendChild(copy);
  const close = deps.el("button", "secondary scenario-device-picker-close", "×");
  close.type = "button";
  deps.setAttr(close, "aria-label", "Закрыть выбор устройства");
  head.appendChild(close);
  dialog.appendChild(head);
  const search = deps.el("input", "scenario-device-picker-search");
  search.type = "search";
  search.placeholder = "Найти по имени, комнате или типу";
  deps.setAttr(search, "aria-label", "Поиск устройства");
  dialog.appendChild(search);
  const filters = deps.el("div", "scenario-device-picker-filters");
  dialog.appendChild(filters);
  const content = deps.el("div", "scenario-device-picker-content");
  dialog.appendChild(content);
  overlay.appendChild(dialog);
  const dismiss = () => overlay.remove();
  close.addEventListener("click", dismiss);
  overlay.addEventListener("click", (event) => { if (event.target === overlay) dismiss(); });
  overlay.addEventListener("keydown", (event) => { if (event.key === "Escape") dismiss(); });
  const renderFilters = () => {
    filters.innerHTML = "";
    const rows = [
      ["Комнаты", "Все комнаты", Array.from(new Set(groups.map((group) => group.roomName))), selectedRoom, (value) => { selectedRoom = value; }],
      ["Типы", "Все типы", Array.from(new Set(groups.map((group) => group.typeName))), selectedType, (value) => { selectedType = value; }],
    ];
    rows.forEach(([title, allLabel, values, selected, choose]) => {
      const row = deps.el("div", "scenario-device-filter-row");
      row.appendChild(deps.el("strong", null, title));
      const choices = deps.el("div", "scenario-device-filter-choices");
      [["", allLabel]].concat(values.sort((left, right) => left.localeCompare(right, "ru")).map((value) => [value, value])).forEach(([value, label]) => {
        const button = deps.el("button", `scenario-device-filter-chip${selected === value ? " is-active" : ""}`, label);
        button.type = "button";
        button.addEventListener("click", () => {
          choose(value);
          renderFilters();
          render();
        });
        choices.appendChild(button);
      });
      row.appendChild(choices);
      filters.appendChild(row);
    });
  };
  const render = () => {
    content.innerHTML = "";
    const query = search.value.trim().toLocaleLowerCase("ru");
    const visible = groups.filter((group) =>
      (!selectedRoom || group.roomName === selectedRoom)
      && (!selectedType || group.typeName === selectedType)
      && (!query || `${group.name} ${group.roomName} ${group.typeName}`.toLocaleLowerCase("ru").includes(query)));
    if (!visible.length) {
      content.appendChild(deps.el("p", "scenario-device-picker-empty", "Устройства не найдены. Измените запрос."));
      return;
    }
    let room = null;
    let type = null;
    let grid = null;
    visible.forEach((group) => {
      if (group.roomName !== room) {
        room = group.roomName;
        type = null;
        content.appendChild(deps.el("h4", "scenario-device-picker-room", room));
      }
      if (group.typeName !== type) {
        type = group.typeName;
        content.appendChild(deps.el("h5", "scenario-device-picker-type", type));
        grid = deps.el("div", "scenario-device-picker-grid");
        content.appendChild(grid);
      }
      const button = deps.el("button", `scenario-device-picker-card${group.entries.some((entry) => entry.target_id === selectedId) ? " is-selected" : ""}`);
      button.type = "button";
      button.appendChild(deps.el("strong", null, group.name));
      button.appendChild(deps.el("span", null, `${group.typeName} · ${group.entries.length} ${group.entries.length === 1 ? "возможность" : "возможности"}`));
      button.addEventListener("click", () => {
        const entry = preferredEntry(group, actionsOnly);
        if (entry) onSelected(entry);
        dismiss();
      });
      grid.appendChild(button);
    });
  };
  search.addEventListener("input", render);
  renderFilters();
  render();
  panel._shell.scenarios.appendChild(overlay);
  search.focus();
}

export function scenarioDeviceButton(panel, rule, deps, devices, actionsOnly, onSelected) {
  const groups = scenarioPhysicalGroups(devices, actionsOnly);
  const selectedGroup = scenarioGroupForTarget(groups, rule.targetId);
  const field = deps.el("div", "scenario-editor-field scenario-device-field");
  field.appendChild(deps.el("span", null, "Устройство"));
  const button = deps.el("button", "scenario-device-select-button");
  button.type = "button";
  button.appendChild(deps.el("strong", null, selectedGroup ? selectedGroup.name : "Выберите устройство"));
  button.appendChild(deps.el("small", null, selectedGroup ? `${selectedGroup.roomName} · ${selectedGroup.typeName}` : "Откроется каталог по комнатам и типам"));
  button.addEventListener("click", () => openPicker(panel, deps, devices, rule.targetId, actionsOnly, onSelected));
  field.appendChild(button);
  return field;
}

export function scenarioLegacyProperty(device) {
  const domain = String(device && device.entity_id || "").split(".")[0];
  const options = ["light", "switch", "fan", "humidifier", "water_heater"].includes(domain)
    ? [{ value: "on", label: "Включено" }, { value: "off", label: "Выключено" }]
    : [];
  return { property_id: "state", label: scenarioCapabilityLabel(device), value_type: options.length ? "enum" : "text", comparisons: ["equals", "not_equals", "changed"], options };
}

export function scenarioDeviceFields(panel, rule, deps, onChange, includeComparison = true, allowChanged = true) {
  const devices = panel._scenarios.catalog && Array.isArray(panel._scenarios.catalog.devices)
    ? panel._scenarios.catalog.devices : [];
  const selected = devices.find((device) => device.target_id === rule.targetId);
  const groups = scenarioPhysicalGroups(devices);
  const selectedGroup = scenarioGroupForTarget(groups, rule.targetId);
  const fragment = deps.el("div", "scenario-rule-fields");
  fragment.appendChild(scenarioDeviceButton(panel, rule, deps, devices, false, (device) => {
    const property = (Array.isArray(device.properties) && device.properties[0]) || scenarioLegacyProperty(device);
    onChange({
      ...rule,
      targetId: device.target_id,
      targetName: device.physical_name || device.name || null,
      property: property.property_id,
      value: null,
      ...(includeComparison ? { comparison: property.comparisons && property.comparisons[0] || "equals" } : {}),
    });
  }));
  if (selectedGroup && selectedGroup.entries.length > 1) {
    fragment.appendChild(scenarioSelectField(
      deps,
      "Событие устройства",
      selected && selected.target_id || "",
      selectedGroup.entries.map((device) => [device.target_id, scenarioCapabilityLabel(device)]),
      (value) => {
        const device = selectedGroup.entries.find((item) => item.target_id === value);
        const property = (device && Array.isArray(device.properties) && device.properties[0]) || scenarioLegacyProperty(device);
        onChange({
          ...rule,
          targetId: value,
          targetName: selectedGroup.name,
          property: property.property_id,
          value: null,
          ...(includeComparison ? { comparison: property.comparisons && property.comparisons[0] || "equals" } : {}),
        });
      },
      "Выберите конкретный датчик, канал или режим внутри физического устройства.",
    ));
  }
  const properties = selected && Array.isArray(selected.properties) && selected.properties.length
    ? selected.properties : selected ? [scenarioLegacyProperty(selected)] : [];
  const normalizedPropertyId = rule.property === "Состояние" ? "state" : rule.property;
  const selectedProperty = properties.find((item) => item.property_id === normalizedPropertyId) || properties[0];
  if (selectedProperty) {
    fragment.appendChild(scenarioSelectField(
      deps,
      "Показатель",
      selectedProperty.property_id,
      properties.map((item) => [item.property_id, item.label]),
      (value) => {
        const property = properties.find((item) => item.property_id === value);
        onChange({
          ...rule,
          property: value,
          value: null,
          ...(includeComparison ? { comparison: property && property.comparisons && property.comparisons[0] || "equals" } : {}),
        });
      },
    ));
  }
  if (includeComparison) {
    const propertyComparisons = selectedProperty && Array.isArray(selectedProperty.comparisons) && selectedProperty.comparisons.length
      ? selectedProperty.comparisons : ["equals", "not_equals", "changed"];
    const allowed = propertyComparisons.filter((value) => allowChanged || value !== "changed");
    const allowedComparisons = COMPARISONS.filter(([value]) => allowed.includes(value));
    const comparison = allowed.includes(rule.comparison) ? rule.comparison : allowed[0];
    fragment.appendChild(scenarioSelectField(deps, "Сравнение", comparison || "equals", allowedComparisons, (value) => onChange({ ...rule, comparison: value, value: value === "changed" ? null : rule.value })));
  }
  if (rule.comparison !== "changed") {
    const stateOptions = selectedProperty && Array.isArray(selectedProperty.options) ? selectedProperty.options : [];
    if (stateOptions.length) {
      const currentValue = String(rule.value || "");
      fragment.appendChild(scenarioSelectField(
        deps,
        selectedProperty.label,
        currentValue,
        [["", "Выберите состояние"]].concat(stateOptions.map((item) => [item.value, item.label])),
        (value) => onChange({ ...rule, value }),
        "Показаны только состояния этой возможности. Сценарий сохраняет исходное значение Home Assistant.",
      ));
    } else if (selectedProperty) {
      const numeric = selectedProperty.value_type === "number";
      fragment.appendChild(scenarioField(deps, `${selectedProperty.label}${selectedProperty.unit ? `, ${selectedProperty.unit}` : ""}`, rule.value || "", (value) => onChange({ ...rule, value }), { placeholder: numeric ? "Введите число" : "Введите значение", type: numeric ? "number" : "text" }));
    }
  }
  return fragment;
}
