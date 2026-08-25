/* Scenario library and editor shared with the Hausman Hub tablet contract. */

import { activeElementWithin, trapModalTabKey } from "./hausman-hub-modal.js?v=1.52.165";
import { eventDataFromDraft, scenarioEditorIssues, scenarioEventFields, scenarioField, scenarioIconField, scenarioSelectField, scenarioToggle } from "./hausman-hub-scenario-fields.js?v=1.52.165";
import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.165";
import { scenarioCapabilityLabel, scenarioDeviceButton, scenarioDeviceFields, scenarioGroupForTarget, scenarioPhysicalGroups } from "./hausman-hub-scenario-device-picker.js?v=1.52.165";
import { groupScenarios, renderScenarioCatalog, scenarioActivationKind, scenarioDisplayGroup, scenarioDisplayText } from "./hausman-hub-scenario-catalog.js?v=1.52.165";

const TRIGGER_TYPES = [
  ["manual", "Ручной запуск"], ["time", "По времени"],
  ["sunrise", "Восход солнца"], ["sunset", "Закат солнца"],
  ["presence", "Присутствие дома"], ["device_state", "Изменение устройства"],
  ["event", "Внешнее событие"],
];
const CONDITION_TYPES = [
  ["device_state", "Состояние устройства"], ["time_window", "Промежуток времени"],
  ["presence", "Присутствие дома"], ["weekday", "Дни недели"],
];
const ACTION_TYPES = [
  ["device_action", "Управление устройством"], ["delay", "Пауза"],
  ["run_scenario", "Запустить сценарий"], ["notification", "Уведомление"],
];
const EDITOR_STEPS = [
  ["about", "Основное"],
  ["triggers", "Когда"],
  ["conditions", "Если"],
  ["actions", "Что сделать"],
  ["review", "Проверка"],
  ["publication", "Публикация"],
];
function scenarioClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function scenarioId() {
  return `scenario_${Date.now().toString(36)}`;
}

function defaultScenarioDraft() {
  return {
    id: scenarioId(), title: "", group: "Мои сценарии", description: "", icon: "mdi:home-heart",
    enabled: true, favorite: false, danger: false, requiresConfirmation: false,
    definition: {
      version: 1, executionMode: "single",
      triggers: [{ id: "trigger-1", type: "manual" }], conditions: [], actions: [],
    },
  };
}

function normalizedScenario(source) {
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
  scenario.definition.triggers = Array.isArray(scenario.definition.triggers)
    && scenario.definition.triggers.length ? scenario.definition.triggers : [{ id: "trigger-1", type: "manual" }];
  scenario.definition.conditions = Array.isArray(scenario.definition.conditions)
    ? scenario.definition.conditions : [];
  scenario.definition.actions = Array.isArray(scenario.definition.actions)
    ? scenario.definition.actions : [];
  return scenario;
}

function localizeSystemScenarioDraft(scenario) {
  if (scenarioActivationKind(scenario) !== "system") return scenario;
  Object.defineProperties(scenario, {
    _sourceTitle: { value: scenario.title, configurable: true },
    _sourceDescription: { value: scenario.description, configurable: true },
    _sourceGroup: { value: scenario.group, configurable: true },
  });
  scenario.title = scenarioDisplayText(scenario.title, scenario);
  scenario.description = scenarioDisplayText(scenario.description, scenario);
  scenario.group = scenarioDisplayGroup(scenario);
  return scenario;
}

function duplicateScenarioDraft(source) {
  const scenario = normalizedScenario(source);
  scenario.id = scenarioId();
  scenario.title = `${scenario.title || "Сценарий"} - копия`;
  scenario.favorite = false;
  delete scenario.updatedAt;
  return scenario;
}

function openScenarioEditor(panel, source) {
  panel._scenarioEditor = localizeSystemScenarioDraft(normalizedScenario(source));
  panel._scenarioEditorOriginal = JSON.stringify(scenarioPayload(panel._scenarioEditor));
  panel._scenarioEditorExpanded = { trigger: 0, condition: 0, action: 0 };
  panel._scenarioEditorRestoreFocus = activeElementWithin(panel);
  panel._scenarioEditorJustOpened = true;
  updateScenarioEditor(panel);
}

function scenarioEditorDirty(panel) {
  if (!panel._scenarioEditor) return false;
  return JSON.stringify(scenarioPayload(panel._scenarioEditor)) !== panel._scenarioEditorOriginal;
}

function closeScenarioEditor(panel) {
  if (scenarioEditorDirty(panel) && !window.confirm("Закрыть редактор без сохранения изменений?")) return false;
  panel._scenarioEditor = null;
  panel._scenarioEditorOriginal = null;
  panel._scenarioEditorExpanded = null;
  panel._scenarioEditorJustOpened = false;
  const restore = panel._scenarioEditorRestoreFocus;
  panel._scenarioEditorRestoreFocus = null;
  updateScenarioEditor(panel);
  if (restore && typeof restore.focus === "function" && restore.isConnected !== false) {
    try { restore.focus(); } catch (error) { /* opener may be gone after a re-render */ }
  }
  return true;
}

function nextScenarioRuleId(prefix, rules) {
  let index = rules.length + 1;
  while (rules.some((rule) => rule.id === `${prefix}-${index}`)) index += 1;
  return `${prefix}-${index}`;
}

function scenarioDevices(panel) {
  const devices = panel._scenarios.catalog && Array.isArray(panel._scenarios.catalog.devices)
    ? panel._scenarios.catalog.devices : [];
  return devices;
}

function updateScenarioEditor(panel) {
  panel._renderScenarios(panel._shell.scenarios);
}

function scenarioCountLabel(value, one, few, many) {
  const absolute = Math.abs(value);
  const word = absolute % 100 >= 11 && absolute % 100 <= 14
    ? many : absolute % 10 === 1 ? one : absolute % 10 >= 2 && absolute % 10 <= 4 ? few : many;
  return `${value} ${word}`;
}

function scenarioRuleTitle(kind, rule, index) {
  const options = kind === "trigger" ? TRIGGER_TYPES : kind === "condition" ? CONDITION_TYPES : ACTION_TYPES;
  const title = options.find(([value]) => value === rule.type)?.[1]
    || (rule.type === "existing_action" ? "Действие центра" : "Не настроено");
  return kind === "action" ? `${index + 1}. ${title}` : title;
}

function scenarioRuleSummary(kind, rule) {
  if (rule.targetName) return [rule.targetName, rule.actionTitle || rule.value].filter(Boolean).join(" · ");
  if (kind === "trigger" && rule.type === "manual") return "Запуск с карточки или панели";
  if (kind === "condition" && rule.type === "presence") return rule.value === "away" ? "Никого нет дома" : "Кто-то дома";
  if (rule.type === "delay") return `Пауза ${rule.delaySeconds || 0} сек.`;
  if (rule.type === "notification") return rule.message || "Текст не задан";
  return String(rule.value || "Откройте, чтобы настроить");
}

function scenarioRuleCard(panel, kind, rule, index, rules, deps) {
  const { el } = deps;
  panel._scenarioEditorExpanded = panel._scenarioEditorExpanded || { trigger: 0, condition: 0, action: 0 };
  const expanded = panel._scenarioEditorExpanded[kind] === index;
  const card = el("article", `scenario-rule-card${expanded ? " is-expanded" : ""}`);
  const head = el("div", "scenario-rule-head");
  const toggle = el("button", "scenario-rule-toggle");
  toggle.type = "button";
  const copy = el("span", "scenario-rule-title");
  copy.appendChild(el("strong", null, scenarioRuleTitle(kind, rule, index)));
  copy.appendChild(el("small", null, scenarioRuleSummary(kind, rule)));
  toggle.appendChild(copy);
  toggle.appendChild(el("span", "scenario-rule-chevron", "⌄"));
  deps.setAttr(toggle, "aria-expanded", String(expanded));
  toggle.addEventListener("click", () => {
    panel._scenarioEditorExpanded[kind] = expanded ? -1 : index;
    updateScenarioEditor(panel);
  });
  head.appendChild(toggle);
  const controls = el("div", "scenario-rule-order");
  const move = (direction, label) => {
    const button = el("button", "secondary scenario-rule-move", label);
    button.type = "button";
    button.disabled = direction < 0 ? index === 0 : index === rules.length - 1;
    deps.setAttr(button, "aria-label", `${direction < 0 ? "Поднять" : "Опустить"} ${kind === "action" ? "шаг" : kind === "trigger" ? "триггер" : "условие"} ${index + 1}`);
    button.addEventListener("click", () => {
      const target = index + direction;
      if (target < 0 || target >= rules.length) return;
      [rules[index], rules[target]] = [rules[target], rules[index]];
      panel._scenarioEditorExpanded[kind] = target;
      updateScenarioEditor(panel);
    });
    return button;
  };
  controls.appendChild(move(-1, "↑"));
  controls.appendChild(move(1, "↓"));
  const remove = el("button", "secondary scenario-rule-remove", "Удалить");
  remove.type = "button";
  remove.addEventListener("click", () => {
    rules.splice(index, 1);
    panel._scenarioEditorExpanded[kind] = rules.length ? Math.min(index, rules.length - 1) : -1;
    updateScenarioEditor(panel);
  });
  controls.appendChild(remove);
  head.appendChild(controls);
  card.appendChild(head);
  const change = (changed) => {
    rules[index] = changed;
    updateScenarioEditor(panel);
  };
  if (!expanded) return card;
  if (kind === "trigger") {
    card.appendChild(scenarioSelectField(deps, "Тип триггера", rule.type, TRIGGER_TYPES, (type) => {
      const value = type === "time" ? "07:30" : ["sunrise", "sunset"].includes(type) ? 0 : type === "presence" ? "home" : null;
      change({ id: rule.id, type, ...(value !== null ? { value } : {}) });
    }));
    if (rule.type === "manual") card.appendChild(el("p", "scenario-rule-hint", "Запускается с карточки, панели или API."));
    if (rule.type === "time") card.appendChild(scenarioField(deps, "Время", rule.value || "", (value) => change({ ...rule, value }), { type: "time" }));
    if (["sunrise", "sunset"].includes(rule.type)) card.appendChild(scenarioField(deps, "Смещение, минут", rule.value ?? 0, (value) => change({ ...rule, value: Number(value) || 0 }), { type: "number" }));
    if (rule.type === "presence") card.appendChild(scenarioSelectField(deps, "Событие", rule.value || "home", [["home", "Кто-то пришёл домой"], ["away", "Все ушли"]], (value) => change({ ...rule, value })));
    if (rule.type === "device_state") card.appendChild(scenarioDeviceFields(panel, rule, deps, change));
    if (rule.type === "event") card.appendChild(scenarioEventFields(deps, rule, change));
  } else if (kind === "condition") {
    card.appendChild(scenarioSelectField(deps, "Тип условия", rule.type, CONDITION_TYPES, (type) => {
      const value = type === "time_window" ? "22:00-07:00" : type === "presence" ? "home" : type === "weekday" ? "пн, вт, ср, чт, пт" : null;
      change({ id: rule.id, type, ...(value !== null ? { value } : {}) });
    }));
    if (rule.type === "presence") card.appendChild(scenarioSelectField(deps, "Состояние дома", rule.value || "home", [["home", "Кто-то дома"], ["away", "Никого нет дома"]], (value) => change({ ...rule, value })));
    if (rule.type === "time_window") card.appendChild(scenarioField(deps, "Промежуток", rule.value || "", (value) => change({ ...rule, value }), { placeholder: "22:00-07:00" }));
    if (rule.type === "weekday") card.appendChild(scenarioField(deps, "Дни недели", rule.value || "", (value) => change({ ...rule, value }), { placeholder: "пн, вт, ср, чт, пт" }));
    if (rule.type === "device_state") card.appendChild(scenarioDeviceFields(panel, rule, deps, change, true, false));
  } else {
    const availableTypes = rule.type === "existing_action" ? ACTION_TYPES.concat([["existing_action", "Действие центра"]]) : ACTION_TYPES;
    card.appendChild(scenarioSelectField(deps, "Тип шага", rule.type, availableTypes, (type) => change({ id: rule.id, type, ...(type === "delay" ? { delaySeconds: 5 } : {}) })));
    if (rule.type === "device_action") {
      const devices = scenarioDevices(panel);
      const selected = devices.find((device) => device.target_id === rule.targetId);
      const groups = scenarioPhysicalGroups(devices, true);
      const selectedGroup = scenarioGroupForTarget(groups, rule.targetId);
      card.appendChild(scenarioDeviceButton(panel, rule, deps, devices, true, (device) => {
        change({ ...rule, targetId: device.target_id, targetName: device.physical_name || device.name || null, actionId: null, actionTitle: null });
      }));
      if (selectedGroup && selectedGroup.entries.length > 1) {
        card.appendChild(scenarioSelectField(
          deps,
          "Возможность устройства",
          selected && selected.target_id || "",
          selectedGroup.entries.map((device) => [device.target_id, scenarioCapabilityLabel(device)]),
          (value) => {
            change({ ...rule, targetId: value, targetName: selectedGroup.name, actionId: null, actionTitle: null });
          },
          "Команды относятся только к выбранному каналу или функции.",
        ));
      }
      const actions = selected && Array.isArray(selected.actions) ? selected.actions : [];
      card.appendChild(scenarioSelectField(deps, "Команда устройства", rule.actionId || "", [["", selected ? "Выберите команду" : "Сначала выберите устройство"]].concat(actions.map((action) => [action.action_id, action.title || action.action_id])), (value) => {
        const action = actions.find((item) => item.action_id === value);
        change({ ...rule, actionId: value || null, actionTitle: action && action.title || null });
      }));
      card.appendChild(scenarioField(deps, "Значение, если требуется", rule.value || "", (value) => change({ ...rule, value: value || null }), { placeholder: "например 75% или 23°C" }));
    }
    if (rule.type === "delay") card.appendChild(scenarioField(deps, "Пауза, секунд", rule.delaySeconds || 5, (value) => change({ ...rule, delaySeconds: Number(value) || 0 }), { type: "number" }));
    if (rule.type === "run_scenario") {
      const items = panel._scenarios.list && panel._scenarios.list.scenarios || [];
      card.appendChild(scenarioSelectField(deps, "Сценарий", rule.scenarioId || "", [["", "Выберите сценарий"]].concat(items.filter((item) => item.id !== panel._scenarioEditor.id).map((item) => [item.id, item.title])), (value) => change({ ...rule, scenarioId: value || null })));
    }
    if (rule.type === "notification") card.appendChild(scenarioField(deps, "Текст уведомления", rule.message || "", (value) => change({ ...rule, message: value }), { multiline: true, placeholder: "Например: окно осталось открыто" }));
    if (rule.type === "existing_action") card.appendChild(el("p", "scenario-rule-hint is-warning", "Сохранённое действие центра. Его можно заменить структурированными шагами."));
  }
  return card;
}

function scenarioEditorSection(deps, title, description) {
  const section = deps.el("section", "scenario-editor-panel");
  section.appendChild(deps.el("h3", null, title));
  if (description) section.appendChild(deps.el("p", "scenario-editor-panel-copy", description));
  return section;
}

function renderScenarioRules(panel, kind, heading, description, items, deps) {
  const { el } = deps;
  const section = scenarioEditorSection(deps, heading);
  const summary = el("div", "scenario-rule-list-summary");
  summary.appendChild(el("span", null, description));
  summary.appendChild(el("b", "scenario-editor-badge", String(items.length)));
  section.appendChild(summary);
  const list = el("div", "scenario-rule-list");
  items.forEach((item, index) => list.appendChild(scenarioRuleCard(panel, kind, item, index, items, deps)));
  if (!items.length) {
    list.appendChild(el("div", "scenario-rule-empty", kind === "condition"
      ? "Без дополнительных условий"
      : kind === "action" ? "Добавьте первое действие, которое выполнит Hausman Hub."
        : "Добавьте хотя бы одно событие запуска."));
  }
  section.appendChild(list);
  const add = el("button", "secondary scenario-add-rule", kind === "trigger" ? "Добавить триггер" : kind === "condition" ? "Добавить условие" : "Добавить шаг");
  add.type = "button";
  add.addEventListener("click", () => {
    const id = nextScenarioRuleId(kind, items);
    if (kind === "trigger") items.push({ id, type: "manual" });
    else if (kind === "condition") items.push({ id, type: "presence", value: "home" });
    else items.push({ id, type: "device_action" });
    panel._scenarioEditorExpanded[kind] = items.length - 1;
    updateScenarioEditor(panel);
  });
  section.appendChild(add);
  return section;
}

function scenarioSummary(scenario) {
  const definition = scenario.definition;
  const trigger = definition.triggers.length === 1 ? "1 триггер" : `${definition.triggers.length} триггера`;
  const conditions = definition.conditions.length ? `${definition.conditions.length} усл.` : "без условий";
  const actions = `${definition.actions.length} действ.`;
  return `${trigger} · ${conditions} · ${actions}`;
}

function scenarioPayload(scenario) {
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
  result.triggerDescription = scenarioSummary(result).split(" · ")[0];
  result.conditionDescription = result.definition.conditions.length ? `${result.definition.conditions.length} условий` : "Без дополнительных условий";
  result.actionDescription = `${result.definition.actions.length} действий`;
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

async function submitScenario(panel, deps, testOnly) {
  const scenario = panel._scenarioEditor;
  const issues = scenarioEditorIssues(scenario);
  if (issues.length) {
    updateScenarioEditor(panel);
    return;
  }
  if (panel._busy) return;
  panel._busy = true;
  panel._notice = "";
  panel._error = false;
  updateScenarioEditor(panel);
  try {
    await panel._hass.callApi("POST", testOnly ? deps.testApi : deps.scenariosApi, scenarioPayload(scenario));
    if (testOnly) {
      panel._notice = `Сценарий «${scenario.title}» прошёл проверку.`;
    } else {
      panel._scenarioEditor = null;
      panel._scenarioEditorOriginal = null;
      panel._scenarioEditorExpanded = null;
      panel._scenarios.list = null;
      panel._notice = `Сценарий «${scenario.title}» сохранён.`;
    }
  } catch (error) {
    panel._error = true;
    panel._notice = testOnly ? "Проверка не пройдена. Исправьте отмеченные поля." : "Сценарий не сохранён. Проверьте заполнение действий.";
  } finally {
    panel._busy = false;
  }
  if (!testOnly && !panel._error) await panel._loadScenarios();
  else updateScenarioEditor(panel);
}

async function saveScenarioQuick(panel, deps, source, successText) {
  if (panel._busy) return false;
  panel._busy = true;
  panel._notice = "";
  try {
    await panel._hass.callApi("POST", deps.scenariosApi, scenarioPayload(normalizedScenario(source)));
    panel._notice = successText;
    panel._error = false;
    await panel._loadScenarios();
    return true;
  } catch (error) {
    panel._notice = "Изменение не сохранено. Проверьте доступность Home Assistant.";
    panel._error = true;
    updateScenarioEditor(panel);
    return false;
  } finally {
    panel._busy = false;
    if (panel._activeSection === "scenarios") panel._render();
  }
}

async function deleteScenarioQuick(panel, deps, scenario) {
  if (panel._busy || !window.confirm(`Удалить сценарий «${scenario.title}»?`)) return;
  panel._busy = true;
  try {
    await panel._hass.callApi("POST", deps.deleteApi, { scenario_id: scenario.id });
    panel._notice = `Сценарий «${scenario.title}» удалён.`;
    panel._error = false;
    await panel._loadScenarios();
  } catch (error) {
    panel._notice = "Сценарий не удалён. Проверьте доступность Home Assistant.";
    panel._error = true;
    updateScenarioEditor(panel);
  } finally {
    panel._busy = false;
    if (panel._activeSection === "scenarios") panel._render();
  }
}

function scenarioEditorStepState(scenario, issues, id) {
  const definition = scenario.definition;
  const stepIssues = (step) => issues.some((issue) => issue.step === step);
  if (id === "about") return [Boolean(String(scenario.title || "").trim()), scenario.title || "Без названия"];
  if (id === "triggers") return [definition.triggers.length > 0 && !stepIssues("triggers"), scenarioCountLabel(definition.triggers.length, "триггер", "триггера", "триггеров")];
  if (id === "conditions") return [!stepIssues("conditions"), definition.conditions.length ? scenarioCountLabel(definition.conditions.length, "условие", "условия", "условий") : "Без условий"];
  if (id === "actions") return [definition.actions.length > 0 && !stepIssues("actions"), scenarioCountLabel(definition.actions.length, "действие", "действия", "действий")];
  if (id === "review") return [issues.length === 0, issues.length ? scenarioCountLabel(issues.length, "ошибка", "ошибки", "ошибок") : "Ошибок нет"];
  return [true, scenario.enabled ? "Включён" : "Выключен"];
}

function scenarioReviewSummary(scenario) {
  const definition = scenario.definition;
  return `Когда: ${scenarioCountLabel(definition.triggers.length, "триггер", "триггера", "триггеров")} · Если: ${definition.conditions.length ? scenarioCountLabel(definition.conditions.length, "условие", "условия", "условий") : "без условий"} · Что сделать: ${scenarioCountLabel(definition.actions.length, "действие", "действия", "действий")}`;
}

function renderScenarioEditor(panel, container, deps) {
  const { el, setAttr } = deps;
  const scenario = panel._scenarioEditor;
  const issues = scenarioEditorIssues(scenario);
  const overlay = el("div", "scenario-editor-overlay");
  const dialog = el("section", "scenario-editor-dialog");
  setAttr(dialog, "role", "dialog");
  setAttr(dialog, "aria-modal", "true");
  setAttr(dialog, "aria-labelledby", "scenario-editor-title");
  dialog.tabIndex = -1;

  const header = el("header", "scenario-editor-header");
  const title = el("div", "scenario-editor-heading");
  const heading = el("h2", null, "Редактор сценария");
  setAttr(heading, "id", "scenario-editor-title");
  title.appendChild(heading);
  title.appendChild(el("p", null, `Сценарий «${scenarioDisplayText(scenario.title, scenario) || "Без названия"}» · ${scenarioCountLabel(scenario.definition.triggers.length, "триггер", "триггера", "триггеров")} · ${scenarioCountLabel(scenario.definition.conditions.length, "условие", "условия", "условий")} · ${scenarioCountLabel(scenario.definition.actions.length, "действие", "действия", "действий")}`));
  header.appendChild(title);
  const badges = el("div", "scenario-editor-header-badges");
  if (scenario.danger || scenario.protected) badges.appendChild(el("span", "scenario-editor-badge is-warning", "Защищённый"));
  badges.appendChild(el("span", "scenario-editor-badge", "Схема v1"));
  header.appendChild(badges);
  const close = el("button", "secondary scenario-editor-close", "×");
  close.type = "button";
  setAttr(close, "aria-label", "Закрыть редактор");
  close.addEventListener("click", () => closeScenarioEditor(panel));
  header.appendChild(close);
  dialog.appendChild(header);

  const steps = el("div", "scenario-editor-steps");
  setAttr(steps, "aria-label", "Готовность сценария");
  EDITOR_STEPS.forEach(([id, label]) => {
    const [ready, detail] = scenarioEditorStepState(scenario, issues, id);
    const step = el("div", `scenario-editor-step${ready ? " is-ready" : ""}`);
    step.appendChild(el("span", "scenario-editor-step-check", "✓"));
    const copy = el("span", "scenario-editor-step-copy");
    copy.appendChild(el("b", null, label));
    copy.appendChild(el("small", null, detail));
    step.appendChild(copy);
    steps.appendChild(step);
  });
  dialog.appendChild(steps);

  const workspace = el("div", "scenario-editor-workspace");
  const left = el("div", "scenario-editor-column scenario-editor-column-about");
  const about = scenarioEditorSection(deps, "О сценарии");
  const grid = el("div", "scenario-editor-grid is-single");
  grid.appendChild(scenarioField(deps, "Название", scenario.title, (value) => { scenario.title = value; }, { placeholder: "Например: Доброе утро", maxlength: 120 }));
  grid.appendChild(scenarioField(deps, "Группа", scenario.group, (value) => { scenario.group = value; }, { placeholder: "Сценарии" }));
  grid.appendChild(scenarioIconField(deps, scenario.icon, (value) => { scenario.icon = value; }));
  grid.appendChild(scenarioField(deps, "Что делает сценарий", scenario.description, (value) => { scenario.description = value; }, { multiline: true, wide: true, maxlength: 500 }));
  about.appendChild(grid);
  left.appendChild(about);
  const execution = scenarioEditorSection(deps, "Выполнение");
  execution.appendChild(scenarioSelectField(deps, "Повторный запуск", scenario.definition.executionMode, [
    ["single", "Один запуск: повтор игнорируется"], ["restart", "Перезапуск: начать заново"], ["queued", "Очередь: выполнить последовательно"],
  ], (value) => { scenario.definition.executionMode = value; }));
  const executionHelp = {
    single: "Новый запуск не прервёт уже выполняющийся сценарий.",
    restart: "Новый запуск остановит текущий и начнёт сценарий заново.",
    queued: "Новые запуски будут выполнены по очереди.",
  };
  execution.appendChild(el("p", "scenario-editor-panel-copy", executionHelp[scenario.definition.executionMode] || executionHelp.single));
  left.appendChild(execution);
  const publication = scenarioEditorSection(deps, "Публикация");
  publication.appendChild(scenarioToggle(deps, "Сценарий включён", "Разрешить автоматический и ручной запуск", scenario.enabled, (value) => { scenario.enabled = value; }));
  publication.appendChild(scenarioToggle(deps, "В быстром доступе", "Показывать карточку среди избранных", scenario.favorite, (value) => { scenario.favorite = value; }));
  publication.appendChild(scenarioToggle(deps, "Подтверждать ручной запуск", scenario.danger ? "Обязательно для защищённого сценария" : "Автозапуск подтверждения не запрашивает", scenario.requiresConfirmation, (value) => { scenario.requiresConfirmation = value; }));
  left.appendChild(publication);
  workspace.appendChild(left);

  const middle = el("div", "scenario-editor-column scenario-editor-column-rules");
  middle.appendChild(renderScenarioRules(panel, "trigger", "Когда", "Любой из триггеров запускает сценарий", scenario.definition.triggers, deps));
  middle.appendChild(renderScenarioRules(panel, "condition", "Только если", "Все условия должны выполняться", scenario.definition.conditions, deps));
  workspace.appendChild(middle);
  const right = el("div", "scenario-editor-column scenario-editor-column-actions");
  right.appendChild(renderScenarioRules(panel, "action", "Выполнить", "Шаги идут строго сверху вниз", scenario.definition.actions, deps));
  workspace.appendChild(right);
  dialog.appendChild(workspace);

  const footer = el("footer", "scenario-editor-footer");
  footer.appendChild(el("p", "scenario-editor-review-summary", scenarioReviewSummary(scenario)));
  const footerRow = el("div", "scenario-editor-footer-row");
  footerRow.appendChild(el("div", issues.length ? "scenario-editor-status is-warning" : "scenario-editor-status is-ready",
    issues.length ? `${issues[0].message}${issues.length > 1 ? ` · ещё ${issues.length - 1}` : ""}` : "Сценарий готов к сохранению"));
  const buttons = el("div", "scenario-editor-footer-actions");
  const test = el("button", "secondary", "Пробный запуск");
  test.disabled = panel._busy || issues.length > 0;
  test.addEventListener("click", () => submitScenario(panel, deps, true));
  const cancel = el("button", "secondary", "Отмена");
  cancel.addEventListener("click", () => closeScenarioEditor(panel));
  const save = el("button", null, panel._busy ? "Сохранение…" : "Сохранить");
  save.disabled = panel._busy || issues.length > 0;
  save.addEventListener("click", () => submitScenario(panel, deps, false));
  buttons.appendChild(test); buttons.appendChild(cancel); buttons.appendChild(save);
  footerRow.appendChild(buttons);
  footer.appendChild(footerRow);
  dialog.appendChild(footer);
  overlay.appendChild(dialog);
  overlay.addEventListener("click", (event) => { if (event.target === overlay) closeScenarioEditor(panel); });
  overlay.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeScenarioEditor(panel);
      return;
    }
    trapModalTabKey(event, dialog);
  });
  container.appendChild(overlay);
  const shouldFocusClose = panel._scenarioEditorJustOpened === true;
  panel._scenarioEditorJustOpened = false;
  Promise.resolve().then(() => {
    if (shouldFocusClose && typeof close.focus === "function") close.focus();
  });
}

export function renderScenarioSection(panel, container, deps) {
  const { el } = deps;
  container.innerHTML = "";
  panel._scenarioLibrary = panel._scenarioLibrary || { filter: "all", roomId: "all", query: "" };
  if (!panel._scenarioLibrary.roomId) panel._scenarioLibrary.roomId = "all";
  const items = panel._scenarios.list && Array.isArray(panel._scenarios.list.scenarios)
    ? panel._scenarios.list.scenarios : [];
  const userItems = items.filter((item) => scenarioActivationKind(item) !== "system");
  const logicalGroups = groupScenarios(userItems);

  container.appendChild(createLibraryHero(panel, {
    eyebrow: "СЦЕНАРИИ ДОМА",
    title: "Дом работает по вашим правилам",
    subtitle: `${userItems.length} сценариев · ${userItems.filter((item) => item.favorite === true).length} на главной · ${userItems.filter((item) => item.enabled === false).length} отключено`,
    facts: logicalGroups.map((group) => ({ label: group.title, value: group.scenarios.length })),
  }, deps));

  const card = el("section", "card scenarios-card scenario-library");
  const heading = el("div", "scenarios-heading scenario-library-toolbar");
  const headingCopy = el("div");
  headingCopy.appendChild(el("h2", null, "Сценарии"));
  headingCopy.appendChild(el("p", "section-intro", `Показано ${userItems.length}`));
  heading.appendChild(headingCopy);
  const create = el("button", "scenario-create", "Новый сценарий");
  create.type = "button";
  create.addEventListener("click", () => openScenarioEditor(panel, defaultScenarioDraft()));
  heading.appendChild(create);
  card.appendChild(heading);
  if (panel._scenarios.loading && !panel._scenarios.list) {
    card.appendChild(el("div", "muted", "Загрузка сценариев…"));
  } else if (!panel._scenarios.list || !Array.isArray(panel._scenarios.list.scenarios)) {
    card.appendChild(el("div", "muted", "Список сценариев недоступен."));
  } else {
    renderScenarioCatalog(panel, card, items, deps, {
      normalize: normalizedScenario,
      open: (scenario) => openScenarioEditor(panel, scenario),
      create: () => openScenarioEditor(panel, defaultScenarioDraft()),
      duplicate: duplicateScenarioDraft,
      save: (scenario, notice) => saveScenarioQuick(panel, deps, scenario, notice),
      delete: (scenario) => deleteScenarioQuick(panel, deps, scenario),
      refresh: () => updateScenarioEditor(panel),
    });
  }
  container.appendChild(card);
  if (panel._scenarioEditor) renderScenarioEditor(panel, container, deps);
}
