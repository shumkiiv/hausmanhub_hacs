/* Confirmed bulk scenario updates and their preview controls. */

import { applyScenarioRoomIds, scenarioRoomOptions } from "./hausman-hub-scenario-rooms.js?v=1.52.194";
import { normalizedScenario, scenarioPayload } from "./hausman-hub-scenario-state.js?v=1.52.194";

export async function bulkSaveScenarios(panel, deps, scenarios, successText) {
  if (panel._busy || !scenarios.length) return false;
  panel._busy = true;
  panel._notice = "";
  const failures = [];
  try {
    for (const scenario of scenarios) {
      try {
        await panel._hass.callApi("POST", deps.scenariosApi, scenarioPayload(normalizedScenario(scenario)));
      } catch (error) {
        failures.push(scenario.title || scenario.id);
      }
    }
    await panel._loadScenarios();
    panel._error = failures.length > 0;
    panel._notice = failures.length
      ? `Изменено ${scenarios.length - failures.length} из ${scenarios.length}. Не сохранены: ${failures.join(", ")}.`
      : successText;
    return failures.length === 0;
  } finally {
    panel._busy = false;
    if (panel._activeSection === "scenarios") panel._render();
  }
}

export function renderScenarioBulkTools(panel, scenarios, deps, handlers) {
  const { el } = deps;
  const state = panel._scenarioLibrary;
  state.selectedIds = state.selectedIds instanceof Set ? state.selectedIds : new Set();
  state.bulkRoomIds = state.bulkRoomIds instanceof Set ? state.bulkRoomIds : new Set();
  const knownIds = new Set(scenarios.map((scenario) => scenario.id));
  state.selectedIds = new Set(Array.from(state.selectedIds).filter((id) => knownIds.has(id)));
  const selected = scenarios.filter((scenario) => state.selectedIds.has(scenario.id));
  if (!selected.length) return null;
  const bulk = el("section", "scenario-bulk-tools");
  bulk.appendChild(el("strong", null, `Выбрано: ${selected.length}`));
  const previewAndSave = (mutate, description) => {
    const drafts = selected.map((source) => {
      const scenario = handlers.normalize(source);
      mutate(scenario);
      return scenario;
    });
    const titles = drafts.slice(0, 5).map((scenario) => scenario.title).join(", ");
    const suffix = drafts.length > 5 ? ` и ещё ${drafts.length - 5}` : "";
    if (!window.confirm(`${description}\n\nБудут изменены: ${titles}${suffix}.\n\nПродолжить?`)) return;
    handlers.bulkSave(drafts, `${description}: ${drafts.length}.`);
    state.selectedIds.clear();
  };
  const enable = el("button", "secondary", "Включить");
  enable.type = "button";
  enable.addEventListener("click", () => previewAndSave((scenario) => { scenario.enabled = true; }, "Включить выбранные сценарии"));
  const disable = el("button", "secondary", "Выключить");
  disable.type = "button";
  disable.addEventListener("click", () => previewAndSave((scenario) => { scenario.enabled = false; }, "Выключить выбранные сценарии"));
  bulk.appendChild(enable);
  bulk.appendChild(disable);
  const roomChoices = el("div", "scenario-bulk-room-choices");
  roomChoices.appendChild(el("span", null, "Перенести в комнаты:"));
  scenarioRoomOptions(panel).forEach(([id, name]) => {
    const label = el("label", "scenario-bulk-room-choice");
    const checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.bulkRoomIds.has(id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.bulkRoomIds.add(id); else state.bulkRoomIds.delete(id);
    });
    label.appendChild(checkbox);
    label.appendChild(el("span", null, name));
    roomChoices.appendChild(label);
  });
  bulk.appendChild(roomChoices);
  const assign = el("button", "secondary", "Применить комнаты");
  assign.type = "button";
  assign.addEventListener("click", () => previewAndSave(
    (scenario) => applyScenarioRoomIds(scenario, Array.from(state.bulkRoomIds)),
    state.bulkRoomIds.size ? `Назначить комнат: ${state.bulkRoomIds.size}` : "Назначить область «Весь дом»",
  ));
  bulk.appendChild(assign);
  const clear = el("button", "secondary", "Снять выбор");
  clear.type = "button";
  clear.addEventListener("click", () => { state.selectedIds.clear(); handlers.refresh(); });
  bulk.appendChild(clear);
  return bulk;
}
