/* Scenario library and editor shared with the HausmanHub tablet contract. */

import { scenarioIconMeta } from "./hausman-hub-scenario-icons.js?v=1.51.69";
import { scenarioField, scenarioIconField, scenarioSelectField, scenarioToggle } from "./hausman-hub-scenario-fields.js?v=1.51.69";

const TRIGGER_TYPES = [
  ["manual", "Ручной запуск"], ["time", "По времени"],
  ["sunrise", "Восход солнца"], ["sunset", "Закат солнца"],
  ["presence", "Присутствие дома"], ["device_state", "Изменение устройства"],
];
const CONDITION_TYPES = [
  ["device_state", "Состояние устройства"], ["time_window", "Промежуток времени"],
  ["presence", "Присутствие дома"], ["weekday", "Дни недели"],
];
const ACTION_TYPES = [
  ["device_action", "Управление устройством"], ["delay", "Пауза"],
  ["run_scenario", "Запустить сценарий"], ["notification", "Уведомление"],
];
const COMPARISONS = [
  ["equals", "равно"], ["not_equals", "не равно"], ["above", "выше"],
  ["below", "ниже"], ["changed", "изменилось"],
];
const EDITOR_STEPS = [
  ["about", "Основное", "Название, иконка и режим выполнения"],
  ["triggers", "Когда", "События, которые запускают сценарий"],
  ["conditions", "Если", "Дополнительные ограничения запуска"],
  ["actions", "Тогда", "Последовательность команд устройствам"],
  ["publication", "Доступ", "Включение, избранное и подтверждение"],
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

function duplicateScenarioDraft(source) {
  const scenario = normalizedScenario(source);
  scenario.id = scenarioId();
  scenario.title = `${scenario.title || "Сценарий"} — копия`;
  scenario.favorite = false;
  delete scenario.updatedAt;
  return scenario;
}

function openScenarioEditor(panel, source) {
  panel._scenarioEditor = normalizedScenario(source);
  panel._scenarioEditorOriginal = JSON.stringify(scenarioPayload(panel._scenarioEditor));
  panel._scenarioEditorStep = "about";
  panel._scenarioEditorJustOpened = true;
  panel._scenarioEditorFocusBody = false;
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
  panel._scenarioEditorStep = null;
  panel._scenarioEditorJustOpened = false;
  panel._scenarioEditorFocusBody = false;
  updateScenarioEditor(panel);
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

function scenarioDeviceFields(panel, rule, deps, onChange, includeComparison = true) {
  const devices = scenarioDevices(panel);
  const targetOptions = [["", "Выберите устройство"]].concat(devices.map((device) => [
    device.target_id, device.name || device.entity_id || device.target_id,
  ]));
  const selected = devices.find((device) => device.target_id === rule.targetId);
  const fragment = deps.el("div", "scenario-rule-fields");
  fragment.appendChild(scenarioSelectField(deps, "Устройство", rule.targetId || "", targetOptions, (value) => {
    const device = devices.find((item) => item.target_id === value);
    onChange({ ...rule, targetId: value || null, targetName: device && device.name || null, property: "Состояние" });
  }, "Выберите физическое устройство, состояние которого нужно учитывать."));
  const properties = ["Состояние"];
  if (selected && Array.isArray(selected.properties)) selected.properties.forEach((item) => properties.push(item));
  fragment.appendChild(scenarioSelectField(deps, "Показатель", rule.property || "Состояние", properties.map((item) => [item, item]), (value) => onChange({ ...rule, property: value })));
  if (includeComparison) {
    fragment.appendChild(scenarioSelectField(deps, "Сравнение", rule.comparison || "equals", COMPARISONS, (value) => onChange({ ...rule, comparison: value })));
  }
  if (rule.comparison !== "changed") {
    fragment.appendChild(scenarioField(deps, "Значение", rule.value || "", (value) => onChange({ ...rule, value }), { placeholder: "on / 23 / открыто" }));
  }
  return fragment;
}

function scenarioRuleCard(panel, kind, rule, index, rules, deps) {
  const { el } = deps;
  const card = el("article", "scenario-rule-card");
  const head = el("div", "scenario-rule-head");
  head.appendChild(el("strong", null, `${index + 1}. ${kind === "action" ? "Шаг" : kind === "trigger" ? "Триггер" : "Условие"}`));
  const remove = el("button", "secondary scenario-rule-remove", "Удалить");
  remove.type = "button";
  remove.addEventListener("click", () => {
    rules.splice(index, 1);
    updateScenarioEditor(panel);
  });
  head.appendChild(remove);
  card.appendChild(head);
  const change = (changed) => {
    rules[index] = changed;
    updateScenarioEditor(panel);
  };
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
  } else if (kind === "condition") {
    card.appendChild(scenarioSelectField(deps, "Тип условия", rule.type, CONDITION_TYPES, (type) => {
      const value = type === "time_window" ? "22:00-07:00" : type === "presence" ? "home" : type === "weekday" ? "пн, вт, ср, чт, пт" : null;
      change({ id: rule.id, type, ...(value !== null ? { value } : {}) });
    }));
    if (rule.type === "presence") card.appendChild(scenarioSelectField(deps, "Состояние дома", rule.value || "home", [["home", "Кто-то дома"], ["away", "Никого нет дома"]], (value) => change({ ...rule, value })));
    if (rule.type === "time_window") card.appendChild(scenarioField(deps, "Промежуток", rule.value || "", (value) => change({ ...rule, value }), { placeholder: "22:00-07:00" }));
    if (rule.type === "weekday") card.appendChild(scenarioField(deps, "Дни недели", rule.value || "", (value) => change({ ...rule, value }), { placeholder: "пн, вт, ср, чт, пт" }));
    if (rule.type === "device_state") card.appendChild(scenarioDeviceFields(panel, rule, deps, change));
  } else {
    const availableTypes = rule.type === "existing_action" ? ACTION_TYPES.concat([["existing_action", "Действие центра"]]) : ACTION_TYPES;
    card.appendChild(scenarioSelectField(deps, "Тип шага", rule.type, availableTypes, (type) => change({ id: rule.id, type, ...(type === "delay" ? { delaySeconds: 5 } : {}) })));
    if (rule.type === "device_action") {
      const devices = scenarioDevices(panel);
      const selected = devices.find((device) => device.target_id === rule.targetId);
      card.appendChild(scenarioSelectField(deps, "Устройство", rule.targetId || "", [["", "Выберите устройство"]].concat(devices.map((device) => [device.target_id, device.name || device.entity_id])), (value) => {
        const device = devices.find((item) => item.target_id === value);
        change({ ...rule, targetId: value || null, targetName: device && device.name || null, actionId: null, actionTitle: null });
      }));
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

function scenarioEditorIssues(scenario) {
  const issues = [];
  const add = (code, step, message) => issues.push({ code, step, message });
  if (!String(scenario.title || "").trim()) add("title_required", "about", "Укажите название сценария.");
  if (!scenario.definition.triggers.length) add("trigger_required", "triggers", "Добавьте хотя бы один триггер.");
  if (!scenario.definition.actions.length) add("action_required", "actions", "Добавьте хотя бы одно действие.");
  scenario.definition.actions.forEach((action) => {
    if (action.type === "device_action" && (!action.targetId || !action.actionId)) add("device_action_incomplete", "actions", "Для каждого действия выберите устройство и команду.");
    if (action.type === "run_scenario" && !action.scenarioId) add("scenario_action_incomplete", "actions", "Выберите запускаемый сценарий.");
    if (action.type === "notification" && !String(action.message || "").trim()) add("notification_empty", "actions", "Введите текст уведомления.");
    if (action.type === "delay" && Number(action.delaySeconds) < 1) add("delay_invalid", "actions", "Пауза должна быть не меньше одной секунды.");
  });
  return [...new Map(issues.map((issue) => [issue.code, issue])).values()];
}

function scenarioStepIssueCount(scenario, step) {
  return scenarioEditorIssues(scenario).filter((issue) => issue.step === step).length;
}

function renderScenarioRules(panel, kind, heading, description, items, deps) {
  const { el } = deps;
  const section = scenarioEditorSection(deps, heading, description);
  const list = el("div", "scenario-rule-list");
  items.forEach((item, index) => list.appendChild(scenarioRuleCard(panel, kind, item, index, items, deps)));
  if (!items.length) {
    list.appendChild(el("div", "scenario-rule-empty", kind === "condition"
      ? "Условия не заданы — сценарий сможет запускаться при любом состоянии дома."
      : kind === "action" ? "Добавьте первое действие, которое выполнит HausmanHub."
        : "Добавьте хотя бы одно событие запуска."));
  }
  section.appendChild(list);
  const add = el("button", "secondary scenario-add-rule", kind === "trigger" ? "+ Добавить триггер" : kind === "condition" ? "+ Добавить условие" : "+ Добавить действие");
  add.type = "button";
  add.addEventListener("click", () => {
    const id = nextScenarioRuleId(kind, items);
    if (kind === "trigger") items.push({ id, type: "manual" });
    else if (kind === "condition") items.push({ id, type: "presence", value: "home" });
    else items.push({ id, type: "device_action" });
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
  result.requiresConfirmation = result.danger || result.requiresConfirmation;
  result.triggerDescription = scenarioSummary(result).split(" · ")[0];
  result.conditionDescription = result.definition.conditions.length ? `${result.definition.conditions.length} условий` : "Без дополнительных условий";
  result.actionDescription = `${result.definition.actions.length} действий`;
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
      panel._scenarioEditorStep = null;
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

function renderScenarioEditor(panel, container, deps) {
  const { el, setAttr } = deps;
  const scenario = panel._scenarioEditor;
  const activeStep = EDITOR_STEPS.some(([id]) => id === panel._scenarioEditorStep)
    ? panel._scenarioEditorStep : "about";
  panel._scenarioEditorStep = activeStep;
  const overlay = el("div", "scenario-editor-overlay");
  const dialog = el("section", "scenario-editor-dialog");
  setAttr(dialog, "role", "dialog");
  setAttr(dialog, "aria-modal", "true");
  setAttr(dialog, "aria-labelledby", "scenario-editor-title");
  dialog.tabIndex = -1;
  const header = el("header", "scenario-editor-header");
  const title = el("div");
  title.appendChild(el("span", "scenario-editor-kicker", "РЕДАКТОР СЦЕНАРИЯ"));
  const heading = el("h2", null, scenario.title || "Новый сценарий");
  setAttr(heading, "id", "scenario-editor-title");
  title.appendChild(heading);
  title.appendChild(el("p", null, "Настройте запуск, условия и последовательность действий."));
  header.appendChild(title);
  header.appendChild(el("span", `scenario-editor-save-state${scenarioEditorDirty(panel) ? " is-dirty" : ""}`,
    scenarioEditorDirty(panel) ? "Есть несохранённые изменения" : "Изменения сохранены"));
  const close = el("button", "secondary scenario-editor-close");
  close.type = "button";
  close.textContent = "×";
  setAttr(close, "aria-label", "Закрыть редактор");
  close.addEventListener("click", () => closeScenarioEditor(panel));
  header.appendChild(close);
  dialog.appendChild(header);

  const workspace = el("div", "scenario-editor-workspace");
  const navigation = el("nav", "scenario-editor-nav");
  setAttr(navigation, "aria-label", "Шаги настройки сценария");
  const flow = el("div", "scenario-editor-flow");
  flow.appendChild(el("strong", null, "Когда → Если → Тогда"));
  flow.appendChild(el("small", null, scenarioSummary(scenario)));
  navigation.appendChild(flow);
  EDITOR_STEPS.forEach(([id, label, description], index) => {
    const button = el("button", `scenario-editor-step${id === activeStep ? " is-active" : ""}`);
    button.type = "button";
    if (id === activeStep) setAttr(button, "aria-current", "step");
    button.appendChild(el("span", "scenario-editor-step-index", String(index + 1)));
    const copy = el("span", "scenario-editor-step-copy");
    copy.appendChild(el("b", null, label));
    copy.appendChild(el("small", null, description));
    button.appendChild(copy);
    const count = scenarioStepIssueCount(scenario, id);
    if (count) button.appendChild(el("span", "scenario-editor-step-issue", String(count)));
    button.addEventListener("click", () => { panel._scenarioEditorStep = id; panel._scenarioEditorFocusBody = true; updateScenarioEditor(panel); });
    navigation.appendChild(button);
  });
  workspace.appendChild(navigation);
  const body = el("div", "scenario-editor-body");
  body.tabIndex = -1;
  if (activeStep === "about") {
    const about = scenarioEditorSection(deps, "Основное", "Название и визуальное обозначение на панели и планшете.");
    const grid = el("div", "scenario-editor-grid");
    grid.appendChild(scenarioField(deps, "Название", scenario.title, (value) => { scenario.title = value; }, { placeholder: "Например: Доброе утро", maxlength: 120 }));
    grid.appendChild(scenarioField(deps, "Группа", scenario.group, (value) => { scenario.group = value; }, { placeholder: "Сценарии" }));
    grid.appendChild(scenarioIconField(deps, scenario.icon, (value) => { scenario.icon = value; }));
    grid.appendChild(scenarioField(deps, "Описание", scenario.description, (value) => { scenario.description = value; }, { multiline: true, wide: true, maxlength: 500 }));
    about.appendChild(grid); body.appendChild(about);
    const execution = scenarioEditorSection(deps, "Повторный запуск", "Как поступить, если сценарий запускается повторно до завершения.");
    execution.appendChild(scenarioSelectField(deps, "Режим выполнения", scenario.definition.executionMode, [
      ["single", "Один запуск — повтор игнорируется"], ["restart", "Перезапуск — начать заново"], ["queued", "Очередь — выполнить последовательно"],
    ], (value) => { scenario.definition.executionMode = value; }));
    body.appendChild(execution);
  } else if (activeStep === "triggers") {
    body.appendChild(renderScenarioRules(panel, "trigger", "Когда запускать", "Выберите одно или несколько событий. Любое из них сможет запустить сценарий.", scenario.definition.triggers, deps));
  } else if (activeStep === "conditions") {
    body.appendChild(renderScenarioRules(panel, "condition", "Дополнительные условия", "Сценарий продолжит выполнение, только если все условия соблюдены.", scenario.definition.conditions, deps));
  } else if (activeStep === "actions") {
    body.appendChild(renderScenarioRules(panel, "action", "Что выполнить", "Команды выполняются сверху вниз, а результат устройства подтверждается Home Assistant.", scenario.definition.actions, deps));
  } else {
    const publication = scenarioEditorSection(deps, "Доступ и безопасность", "Настройте доступность сценария и его отображение на главном экране.");
    publication.appendChild(scenarioToggle(deps, "Сценарий включён", "Разрешить автоматический и ручной запуск", scenario.enabled, (value) => { scenario.enabled = value; }));
    publication.appendChild(scenarioToggle(deps, "Показывать на главной", "Добавить сценарий в быстрый доступ", scenario.favorite, (value) => { scenario.favorite = value; }));
    publication.appendChild(scenarioToggle(deps, "Требовать подтверждение", "Перед опасным запуском показать понятное подтверждение", scenario.requiresConfirmation, (value) => { scenario.requiresConfirmation = value; }));
    body.appendChild(publication);
  }
  workspace.appendChild(body);
  dialog.appendChild(workspace);

  const footer = el("footer", "scenario-editor-footer");
  const issues = scenarioEditorIssues(scenario);
  footer.appendChild(el("div", issues.length ? "scenario-editor-status is-warning" : "scenario-editor-status is-ready",
    issues[0]?.message || (scenarioEditorDirty(panel) ? "Готово к проверке и сохранению" : "Все изменения сохранены")));
  const buttons = el("div", "scenario-editor-footer-actions");
  const activeIndex = EDITOR_STEPS.findIndex(([id]) => id === activeStep);
  const previous = el("button", "secondary scenario-editor-previous", "Назад");
  previous.type = "button"; previous.disabled = activeIndex === 0;
  previous.addEventListener("click", () => { panel._scenarioEditorStep = EDITOR_STEPS[Math.max(0, activeIndex - 1)][0]; panel._scenarioEditorFocusBody = true; updateScenarioEditor(panel); });
  const next = el("button", "secondary scenario-editor-next", activeIndex === EDITOR_STEPS.length - 1 ? "К началу" : "Далее");
  next.type = "button";
  next.addEventListener("click", () => { panel._scenarioEditorStep = EDITOR_STEPS[(activeIndex + 1) % EDITOR_STEPS.length][0]; panel._scenarioEditorFocusBody = true; updateScenarioEditor(panel); });
  const test = el("button", "secondary", "Проверить");
  test.disabled = panel._busy;
  test.addEventListener("click", () => submitScenario(panel, deps, true));
  const cancel = el("button", "secondary", "Отмена");
  cancel.addEventListener("click", () => closeScenarioEditor(panel));
  const save = el("button", null, panel._busy ? "Сохранение…" : "Сохранить");
  save.disabled = panel._busy;
  save.addEventListener("click", () => submitScenario(panel, deps, false));
  buttons.appendChild(previous); buttons.appendChild(next); buttons.appendChild(test); buttons.appendChild(cancel); buttons.appendChild(save);
  footer.appendChild(buttons);
  dialog.appendChild(footer);
  overlay.appendChild(dialog);
  overlay.addEventListener("click", (event) => { if (event.target === overlay) closeScenarioEditor(panel); });
  overlay.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeScenarioEditor(panel);
    }
  });
  container.appendChild(overlay);
  const shouldFocusClose = panel._scenarioEditorJustOpened === true;
  const shouldFocusBody = panel._scenarioEditorFocusBody === true;
  panel._scenarioEditorJustOpened = false;
  panel._scenarioEditorFocusBody = false;
  Promise.resolve().then(() => {
    if (shouldFocusClose && typeof close.focus === "function") close.focus();
    else if (shouldFocusBody && typeof body.focus === "function") body.focus();
  });
}

export function renderScenarioSection(panel, container, deps) {
  const { el, svgIcon, setAttr } = deps;
  container.innerHTML = "";
  panel._scenarioLibrary = panel._scenarioLibrary || { filter: "all", query: "" };
  const items = panel._scenarios.list && Array.isArray(panel._scenarios.list.scenarios)
    ? panel._scenarios.list.scenarios : [];
  const enabledCount = items.filter((item) => item.enabled !== false).length;
  const favoriteCount = items.filter((item) => item.favorite === true).length;

  const hero = el("section", "scenario-library-hero");
  const heroCopy = el("div", "scenario-library-hero-copy");
  heroCopy.appendChild(el("span", "scenario-library-kicker", "СЦЕНАРИИ ДОМА"));
  heroCopy.appendChild(el("h2", null, "Дом работает по вашим правилам"));
  heroCopy.appendChild(el("p", null, "Собирайте устройства, условия и расписание в понятные действия. Каждый запуск подтверждается Home Assistant."));
  hero.appendChild(heroCopy);
  const heroStats = el("div", "scenario-library-stats");
  [[String(items.length), "всего"], [String(enabledCount), "включено"], [String(favoriteCount), "на главной"]].forEach(([value, label]) => {
    const stat = el("div"); stat.appendChild(el("strong", null, value)); stat.appendChild(el("span", null, label)); heroStats.appendChild(stat);
  });
  hero.appendChild(heroStats);
  container.appendChild(hero);

  const card = el("section", "card scenarios-card scenario-library");
  const heading = el("div", "scenarios-heading scenario-library-toolbar");
  const headingCopy = el("div");
  headingCopy.appendChild(el("h2", null, "Мои сценарии"));
  headingCopy.appendChild(el("p", "section-intro", "Быстрый запуск и полное редактирование без технических сущностей"));
  heading.appendChild(headingCopy);
  const create = el("button", "scenario-create", "+ Новый сценарий");
  create.type = "button";
  create.addEventListener("click", () => openScenarioEditor(panel, defaultScenarioDraft()));
  heading.appendChild(create);
  card.appendChild(heading);
  const controls = el("div", "scenario-library-controls");
  const search = el("input", "scenario-library-search");
  search.type = "search";
  search.value = panel._scenarioLibrary.query;
  setAttr(search, "placeholder", "Найти сценарий");
  setAttr(search, "aria-label", "Найти сценарий");
  controls.appendChild(search);
  const filters = el("div", "scenario-library-filters");
  const filterOptions = [["all", "Все"], ["favorite", "На главной"], ["enabled", "Включены"], ["disabled", "Выключены"]];
  filterOptions.forEach(([value, label]) => {
    const button = el("button", panel._scenarioLibrary.filter === value ? "is-active" : "", label);
    button.type = "button";
    button.addEventListener("click", () => { panel._scenarioLibrary.filter = value; updateScenarioEditor(panel); });
    filters.appendChild(button);
  });
  controls.appendChild(filters);
  card.appendChild(controls);
  if (panel._scenarios.loading && !panel._scenarios.list) {
    card.appendChild(el("div", "muted", "Загрузка сценариев…"));
  } else if (!panel._scenarios.list || !Array.isArray(panel._scenarios.list.scenarios)) {
    card.appendChild(el("div", "muted", "Список сценариев недоступен."));
  } else {
    const query = panel._scenarioLibrary.query.trim().toLocaleLowerCase("ru");
    const filtered = items.filter((scenario) => {
      const matchesQuery = !query || `${scenario.title || ""} ${scenario.group || ""} ${scenario.description || ""}`.toLocaleLowerCase("ru").includes(query);
      const matchesFilter = panel._scenarioLibrary.filter === "all"
        || (panel._scenarioLibrary.filter === "favorite" && scenario.favorite === true)
        || (panel._scenarioLibrary.filter === "enabled" && scenario.enabled !== false)
        || (panel._scenarioLibrary.filter === "disabled" && scenario.enabled === false);
      return matchesQuery && matchesFilter;
    });
    if (!items.length) {
      const empty = el("div", "scenario-empty");
      const icon = el("span", "scenario-empty-icon");
      icon.appendChild(svgIcon("bolt"));
      empty.appendChild(icon);
      empty.appendChild(el("h3", null, "Создайте первый сценарий"));
      empty.appendChild(el("p", null, "Объедините команды устройств, расписание и условия в одно понятное действие."));
      const emptyCreate = el("button", "secondary", "Создать сценарий");
      emptyCreate.addEventListener("click", () => openScenarioEditor(panel, defaultScenarioDraft()));
      empty.appendChild(emptyCreate);
      card.appendChild(empty);
    } else {
      const empty = el("div", "scenario-empty scenario-empty-compact");
      empty.appendChild(el("h3", null, "Ничего не найдено"));
      empty.appendChild(el("p", null, "Измените запрос или выберите другой фильтр."));
      empty.hidden = filtered.length > 0;
      card.appendChild(empty);
      panel._scenarioEmptySearch = empty;
    }
    const list = el("div", "scenario-list scenario-library-grid");
    const visibleIds = new Set(filtered.map((item) => item.id));
    items.forEach((source) => {
      const scenario = normalizedScenario(source);
      const requiresConfirmation = scenario.requiresConfirmation === true || scenario.requires_confirmation === true;
      const meta = scenarioIconMeta(scenario.icon, scenario.title);
      const row = el("article", `scenario-row scenario-library-card${scenario.enabled ? "" : " is-disabled"}`);
      row.hidden = !visibleIds.has(source.id);
      row._scenarioSearchText = `${scenario.title} ${scenario.group} ${scenario.description}`.toLocaleLowerCase("ru");
      row._scenarioState = scenario;
      const top = el("div", "scenario-library-card-top");
      const icon = el("span", "scenario-icon scenario-library-icon");
      const materialIcon = el("ha-icon", "icon scenario-material-icon");
      setAttr(materialIcon, "icon", `mdi:${meta.mdi}`);
      icon.appendChild(materialIcon);
      top.appendChild(icon);
      const favorite = el("button", `scenario-favorite${scenario.favorite ? " is-active" : ""}`);
      favorite.type = "button";
      favorite.textContent = scenario.favorite ? "★" : "☆";
      setAttr(favorite, "aria-label", scenario.favorite ? "Убрать с главного экрана" : "Добавить на главный экран");
      favorite.addEventListener("click", () => {
        scenario.favorite = !scenario.favorite;
        saveScenarioQuick(panel, deps, scenario, scenario.favorite ? `Сценарий «${scenario.title}» добавлен на главный экран.` : `Сценарий «${scenario.title}» убран с главного экрана.`);
      });
      top.appendChild(favorite);
      row.appendChild(top);
      const copy = el("div", "scenario-copy scenario-library-copy");
      copy.tabIndex = 0;
      copy.appendChild(el("span", "scenario-library-group", scenario.group));
      copy.appendChild(el("h3", null, scenario.title || scenario.id));
      copy.appendChild(el("p", null, scenario.description || "Сценарий готов к ручному или автоматическому запуску."));
      copy.appendChild(el("small", "scenario-library-summary", [scenarioSummary(scenario), requiresConfirmation ? "с подтверждением" : ""].filter(Boolean).join(" · ")));
      copy.addEventListener("click", () => openScenarioEditor(panel, scenario));
      copy.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openScenarioEditor(panel, scenario);
        }
      });
      row.appendChild(copy);
      const actions = el("div", "scenario-actions scenario-library-actions");
      const enabled = el("button", `scenario-enabled${scenario.enabled ? " is-active" : ""}`, scenario.enabled ? "Включён" : "Выключен");
      enabled.type = "button";
      enabled.addEventListener("click", () => {
        scenario.enabled = !scenario.enabled;
        saveScenarioQuick(panel, deps, scenario, `Сценарий «${scenario.title}» ${scenario.enabled ? "включён" : "выключен"}.`);
      });
      actions.appendChild(enabled);
      const edit = el("button", "secondary scenario-edit", "Изменить");
      edit.type = "button";
      edit.addEventListener("click", () => openScenarioEditor(panel, scenario));
      actions.appendChild(edit);
      const menu = el("details", "scenario-more");
      const menuButton = el("summary", null, "•••");
      setAttr(menuButton, "aria-label", `Дополнительные действия для «${scenario.title}»`);
      menu.appendChild(menuButton);
      const menuItems = el("div", "scenario-more-menu");
      const test = el("button", null, "Проверить");
      test.type = "button"; test.addEventListener("click", () => panel._scenarioTest(scenario));
      const duplicate = el("button", null, "Создать копию");
      duplicate.type = "button"; duplicate.addEventListener("click", () => {
        openScenarioEditor(panel, duplicateScenarioDraft(scenario));
      });
      const remove = el("button", "is-danger", "Удалить");
      remove.type = "button"; remove.addEventListener("click", () => deleteScenarioQuick(panel, deps, scenario));
      menuItems.appendChild(test); menuItems.appendChild(duplicate); menuItems.appendChild(remove);
      menu.appendChild(menuItems); actions.appendChild(menu);
      const run = el("button", "scenario-run");
      run.type = "button";
      run.disabled = panel._busy || !scenario.enabled;
      run.appendChild(svgIcon("play"));
      setAttr(run, "aria-label", scenario.enabled ? `Запустить сценарий «${scenario.title}»` : `Сценарий «${scenario.title}» выключен`);
      run.addEventListener("click", () => panel._post(deps.runApi, { scenario_id: scenario.id }, requiresConfirmation ? `Запустить сценарий «${scenario.title}»?` : null));
      actions.appendChild(run);
      row.appendChild(actions);
      list.appendChild(row);
    });
    card.appendChild(list);
    search.addEventListener("input", () => {
      panel._scenarioLibrary.query = search.value;
      const nextQuery = search.value.trim().toLocaleLowerCase("ru");
      let visibleCount = 0;
      Array.from(list.children).forEach((row) => {
        const scenario = row._scenarioState;
        const matchesQuery = !nextQuery || row._scenarioSearchText.includes(nextQuery);
        const matchesFilter = panel._scenarioLibrary.filter === "all"
          || (panel._scenarioLibrary.filter === "favorite" && scenario.favorite === true)
          || (panel._scenarioLibrary.filter === "enabled" && scenario.enabled !== false)
          || (panel._scenarioLibrary.filter === "disabled" && scenario.enabled === false);
        row.hidden = !(matchesQuery && matchesFilter);
        if (!row.hidden) visibleCount += 1;
      });
      if (panel._scenarioEmptySearch) panel._scenarioEmptySearch.hidden = visibleCount > 0;
    });
  }
  container.appendChild(card);
  if (panel._scenarioEditor) renderScenarioEditor(panel, container, deps);
}
