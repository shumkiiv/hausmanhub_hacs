/* Room-scope projection and controls shared by the scenario editor/catalog. */

export function scenarioRoomIds(scenario) {
  if (Array.isArray(scenario && scenario.roomIds)) {
    return Array.from(new Set(scenario.roomIds.filter((value) => typeof value === "string" && value)));
  }
  return scenario && typeof scenario.roomId === "string" && scenario.roomId
    ? [scenario.roomId] : [];
}

export function scenarioRoomOptions(panel, scenarios = null) {
  const names = new Map();
  const devices = panel._scenarios && panel._scenarios.catalog
    && Array.isArray(panel._scenarios.catalog.devices) ? panel._scenarios.catalog.devices : [];
  devices.forEach((device) => {
    if (device.room_id) names.set(device.room_id, device.room_name || device.room_id);
  });
  const rooms = panel._homeDashboard && Array.isArray(panel._homeDashboard.rooms)
    ? panel._homeDashboard.rooms : [];
  rooms.forEach((room) => { if (room.id) names.set(room.id, room.name || room.id); });
  if (Array.isArray(scenarios)) {
    const used = new Set(scenarios.flatMap(scenarioRoomIds));
    return Array.from(names, ([id, name]) => [id, name])
      .filter(([id]) => used.has(id))
      .sort((left, right) => left[1].localeCompare(right[1], "ru"));
  }
  return Array.from(names, ([id, name]) => [id, name])
    .sort((left, right) => left[1].localeCompare(right[1], "ru"));
}

export function applyScenarioRoomIds(scenario, values) {
  const roomIds = Array.from(new Set((values || []).filter(Boolean)));
  scenario.roomIds = roomIds;
  scenario.roomId = roomIds[0] || null;
}

export function scenarioRoomLabels(panel, scenario) {
  const names = new Map(scenarioRoomOptions(panel));
  const roomIds = scenarioRoomIds(scenario);
  return roomIds.length ? roomIds.map((id) => names.get(id) || id) : ["Весь дом"];
}

export function scenarioAffectedDeviceCount(panel, scenario) {
  const devices = panel._scenarios && panel._scenarios.catalog
    && Array.isArray(panel._scenarios.catalog.devices) ? panel._scenarios.catalog.devices : [];
  const byTarget = new Map(devices.map((device) => [device.target_id, device]));
  const actions = scenario && scenario.definition && Array.isArray(scenario.definition.actions)
    ? scenario.definition.actions : [];
  return new Set(actions.map((action) => byTarget.get(action.targetId))
    .filter(Boolean).map((device) => device.physical_id || device.target_id)).size;
}

export function renderScenarioRoomPicker(panel, scenario, deps, onChange) {
  const field = deps.el("div", "scenario-editor-field scenario-room-picker is-wide");
  field.appendChild(deps.el("span", null, "Комнаты сценария"));
  const selected = new Set(scenarioRoomIds(scenario));
  const options = scenarioRoomOptions(panel);
  const controls = deps.el("div", "scenario-room-picker-controls");
  const wholeHome = deps.el("button", `scenario-room-chip${selected.size ? "" : " is-active"}`, "Весь дом");
  wholeHome.type = "button";
  wholeHome.addEventListener("click", () => {
    applyScenarioRoomIds(scenario, []);
    onChange();
  });
  controls.appendChild(wholeHome);
  options.forEach(([id, name]) => {
    const button = deps.el("button", `scenario-room-chip${selected.has(id) ? " is-active" : ""}`, name);
    button.type = "button";
    deps.setAttr(button, "aria-pressed", String(selected.has(id)));
    button.addEventListener("click", () => {
      if (selected.has(id)) selected.delete(id); else selected.add(id);
      applyScenarioRoomIds(scenario, Array.from(selected));
      onChange();
    });
    controls.appendChild(button);
  });
  field.appendChild(controls);
  const shortcuts = deps.el("div", "scenario-room-picker-shortcuts");
  const selectAll = deps.el("button", "secondary", "Выбрать все");
  selectAll.type = "button";
  selectAll.disabled = !options.length || selected.size === options.length;
  selectAll.addEventListener("click", () => {
    applyScenarioRoomIds(scenario, options.map(([id]) => id));
    onChange();
  });
  const clear = deps.el("button", "secondary", "Очистить");
  clear.type = "button";
  clear.disabled = selected.size === 0;
  clear.addEventListener("click", () => {
    applyScenarioRoomIds(scenario, []);
    onChange();
  });
  shortcuts.appendChild(selectAll);
  shortcuts.appendChild(clear);
  field.appendChild(shortcuts);
  field.appendChild(deps.el("small", null, selected.size
    ? `Выбрано комнат: ${selected.size}`
    : "Весь дом: сценарий не ограничен одной комнатой."));
  return field;
}
