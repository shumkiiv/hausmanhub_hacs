/* Pure scenario editor state, compatibility projection and save payload. */

import { eventDataFromDraft } from "./hausman-hub-scenario-fields.js?v=1.52.216";
import { scenarioDisplayGroup, scenarioDisplayText } from "./hausman-hub-scenario-catalog.js?v=1.52.216";
import { applyScenarioRoomIds, scenarioRoomIds } from "./hausman-hub-scenario-rooms.js?v=1.52.216";

export function scenarioClone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function defaultScenarioDraft() {
  return {
    id: `scenario_${Date.now().toString(36)}`,
    title: "",
    group: "Мои сценарии",
    description: "",
    icon: "mdi:home-heart",
    enabled: true,
    favorite: false,
    danger: false,
    requiresConfirmation: false,
    roomId: null,
    roomIds: [],
    definition: {
      version: 1,
      executionMode: "single",
      executionBackend: "hausman",
      triggers: [{ id: "trigger-1", type: "manual" }],
      conditions: [],
      actions: [],
    },
  };
}

export function normalizedScenario(source) {
  const scenario = scenarioClone(source || defaultScenarioDraft());
  scenario.group = scenario.group || "Сценарии";
  scenario.description = scenario.description || "";
  scenario.icon = scenario.icon || "mdi:script";
  scenario.enabled = scenario.enabled !== false;
  scenario.favorite = scenario.favorite === true;
  scenario.danger = scenario.danger === true;
  scenario.requiresConfirmation = scenario.requiresConfirmation === true
    || scenario.requires_confirmation === true || scenario.danger;
  scenario.definition = scenario.definition || {};
  scenario.definition.version = 1;
  scenario.definition.executionMode = scenario.definition.executionMode || "single";
  scenario.definition.executionBackend = scenario.definition.executionBackend || "hausman";
  if (scenario.definition.executionBackend === "node_red") {
    scenario.definition.nodeRed = scenario.definition.nodeRed || {
      generatedBy: "hausman", syncStatus: "pending", inputTargetIds: [],
    };
    scenario.definition.nodeRed.inputTargetIds = Array.isArray(scenario.definition.nodeRed.inputTargetIds)
      ? scenario.definition.nodeRed.inputTargetIds : [];
  } else {
    delete scenario.definition.nodeRed;
  }
  scenario.definition.triggers = Array.isArray(scenario.definition.triggers)
    && scenario.definition.triggers.length ? scenario.definition.triggers : [{ id: "trigger-1", type: "manual" }];
  scenario.definition.conditions = Array.isArray(scenario.definition.conditions)
    ? scenario.definition.conditions : [];
  scenario.definition.actions = Array.isArray(scenario.definition.actions)
    ? scenario.definition.actions : [];
  applyScenarioRoomIds(scenario, scenarioRoomIds(scenario));
  return scenario;
}

function isNodeRedSafetyPlaceholder(action) {
  return action && action.id === "safe_placeholder"
    && action.type === "delay" && Number(action.delaySeconds) === 1;
}

export function scenarioHasDynamicNodeRedPlan(scenario) {
  const definition = scenario && scenario.definition || {};
  return definition.executionBackend === "node_red"
    && Array.isArray(definition.actions)
    && definition.actions.some(isNodeRedSafetyPlaceholder);
}

export function scenarioVisibleActions(scenario) {
  const definition = scenario && scenario.definition || {};
  const actions = Array.isArray(definition.actions) ? definition.actions : [];
  return scenarioHasDynamicNodeRedPlan(scenario)
    ? actions.filter((action) => !isNodeRedSafetyPlaceholder(action)) : actions;
}

function countLabel(value, one, few, many) {
  const absolute = Math.abs(value);
  const word = absolute % 100 >= 11 && absolute % 100 <= 14
    ? many : absolute % 10 === 1 ? one : absolute % 10 >= 2 && absolute % 10 <= 4 ? few : many;
  return `${value} ${word}`;
}

export function scenarioActionDetail(scenario) {
  if (scenarioHasDynamicNodeRedPlan(scenario)) return "Алгоритм Node-RED";
  return countLabel(scenarioVisibleActions(scenario).length, "действие", "действия", "действий");
}

export function scenarioReviewSummary(scenario) {
  const definition = scenario.definition;
  const conditions = definition.conditions.length
    ? countLabel(definition.conditions.length, "условие", "условия", "условий") : "без условий";
  return `Когда: ${countLabel(definition.triggers.length, "триггер", "триггера", "триггеров")} · Если: ${conditions} · Что сделать: ${scenarioActionDetail(scenario)}`;
}

export function duplicateScenarioDraft(source, options = {}) {
  const scenario = normalizedScenario(source);
  scenario.id = `scenario_${Date.now().toString(36)}`;
  scenario.title = `${scenario.title || "Сценарий"} - копия`;
  scenario.favorite = false;
  if (options.keepRooms === false) applyScenarioRoomIds(scenario, []);
  if (options.keepActions === false) scenario.definition.actions = [];
  delete scenario.updatedAt;
  delete scenario.revision;
  delete scenario.expectedRevision;
  return scenario;
}

export function scenarioSummary(scenario) {
  const definition = scenario.definition;
  const trigger = definition.triggers.length === 1 ? "1 триггер" : `${definition.triggers.length} триггера`;
  const conditions = definition.conditions.length ? `${definition.conditions.length} усл.` : "без условий";
  const actions = scenarioHasDynamicNodeRedPlan(scenario)
    ? "алгоритм Node-RED" : `${definition.actions.length} действ.`;
  return `${trigger} · ${conditions} · ${actions}`;
}

export function scenarioPayload(scenario) {
  const result = scenarioClone(scenario);
  result.title = String(result.title || "").trim();
  result.description = String(result.description || "").trim();
  result.group = String(result.group || "Сценарии").trim();
  if (Object.hasOwn(scenario, "_sourceTitle")
      && result.title === scenarioDisplayText(scenario._sourceTitle, scenario).trim()) {
    result.title = String(scenario._sourceTitle || "").trim();
  }
  if (Object.hasOwn(scenario, "_sourceDescription")
      && result.description === scenarioDisplayText(scenario._sourceDescription, scenario).trim()) {
    result.description = String(scenario._sourceDescription || "").trim();
  }
  if (Object.hasOwn(scenario, "_sourceGroup") && result.group === scenarioDisplayGroup(scenario)) {
    result.group = String(scenario._sourceGroup || "Сценарии").trim();
  }
  result.requiresConfirmation = result.danger || result.requiresConfirmation;
  applyScenarioRoomIds(result, scenarioRoomIds(result));
  if (Number.isInteger(result.revision)) {
    result.expectedRevision = result.revision;
    delete result.revision;
  }
  result.triggerDescription = scenarioSummary(result).split(" · ")[0];
  result.conditionDescription = result.definition.conditions.length
    ? `${result.definition.conditions.length} условий` : "Без дополнительных условий";
  result.actionDescription = scenarioHasDynamicNodeRedPlan(result)
    ? String(result.actionDescription || "Динамический план действий Node-RED").trim()
    : `${result.definition.actions.length} действий`;
  if (result.definition.executionBackend === "node_red") {
    result.definition.nodeRed = result.definition.nodeRed || {
      generatedBy: "hausman", syncStatus: "pending", inputTargetIds: [],
    };
    const referenced = [
      ...result.definition.triggers,
      ...result.definition.conditions,
      ...result.definition.actions,
    ].map((item) => item && item.targetId).filter(Boolean);
    result.definition.nodeRed.inputTargetIds = Array.from(new Set([
      ...(result.definition.nodeRed.inputTargetIds || []), ...referenced,
    ])).slice(0, 32);
  } else {
    result.definition.executionBackend = "hausman";
    delete result.definition.nodeRed;
  }
  result.definition.triggers.forEach((trigger) => {
    if (trigger.type !== "event") return;
    const filter = eventDataFromDraft(trigger);
    if (!filter.error) trigger.eventData = filter.value;
    delete trigger.eventDataText;
  });
  delete result.updatedAt;
  delete result.requires_confirmation;
  return result;
}
