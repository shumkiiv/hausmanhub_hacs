/* Scenario library and editor shared with the HausmanHub tablet contract. */

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
const ICONS = [
  ["mdi:script", "Сценарий"], ["mdi:weather-sunset-up", "Утро"],
  ["mdi:weather-night", "Ночь"], ["mdi:home", "Дом"],
  ["mdi:lightbulb-group", "Освещение"], ["mdi:thermometer", "Климат"],
  ["mdi:shield-home", "Безопасность"], ["mdi:movie-open", "Кино"],
];

function scenarioClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function scenarioId() {
  return `scenario_${Date.now().toString(36)}`;
}

function defaultScenarioDraft() {
  return {
    id: scenarioId(), title: "", group: "Сценарии", description: "", icon: "mdi:script",
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

function scenarioField(deps, label, value, onChange, options = {}) {
  const { el, setAttr } = deps;
  const wrapper = el("label", `scenario-editor-field${options.wide ? " is-wide" : ""}`);
  wrapper.appendChild(el("span", null, label));
  const input = el(options.multiline ? "textarea" : "input");
  input.value = value === null || value === undefined ? "" : String(value);
  if (options.placeholder) setAttr(input, "placeholder", options.placeholder);
  if (options.type) setAttr(input, "type", options.type);
  if (options.maxlength) setAttr(input, "maxlength", String(options.maxlength));
  input.addEventListener("input", () => onChange(input.value));
  wrapper.appendChild(input);
  if (options.help) wrapper.appendChild(el("small", null, options.help));
  return wrapper;
}

function scenarioSelectField(deps, label, value, options, onChange, help = "") {
  const { el } = deps;
  const wrapper = el("label", "scenario-editor-field");
  wrapper.appendChild(el("span", null, label));
  const select = el("select");
  options.forEach(([optionValue, optionLabel]) => {
    const option = el("option", null, optionLabel);
    option.value = optionValue === null ? "" : String(optionValue);
    option.selected = String(value ?? "") === option.value;
    select.appendChild(option);
  });
  select.addEventListener("change", () => onChange(select.value));
  wrapper.appendChild(select);
  if (help) wrapper.appendChild(el("small", null, help));
  return wrapper;
}

function scenarioToggle(deps, label, description, checked, onChange) {
  const { el } = deps;
  const wrapper = el("label", "scenario-editor-toggle");
  const input = el("input");
  input.type = "checkbox";
  input.checked = checked;
  input.addEventListener("change", () => onChange(input.checked));
  const copy = el("span");
  copy.appendChild(el("b", null, label));
  copy.appendChild(el("small", null, description));
  wrapper.appendChild(input);
  wrapper.appendChild(copy);
  return wrapper;
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
  if (!String(scenario.title || "").trim()) issues.push("Укажите название сценария.");
  if (!scenario.definition.triggers.length) issues.push("Добавьте хотя бы один триггер.");
  if (!scenario.definition.actions.length) issues.push("Добавьте хотя бы одно действие.");
  scenario.definition.actions.forEach((action) => {
    if (action.type === "device_action" && (!action.targetId || !action.actionId)) issues.push("Для каждого действия выберите устройство и команду.");
    if (action.type === "run_scenario" && !action.scenarioId) issues.push("Выберите запускаемый сценарий.");
    if (action.type === "notification" && !String(action.message || "").trim()) issues.push("Введите текст уведомления.");
    if (action.type === "delay" && Number(action.delaySeconds) < 1) issues.push("Пауза должна быть не меньше одной секунды.");
  });
  return [...new Set(issues)];
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

function renderScenarioEditor(panel, container, deps) {
  const { el, svgIcon, setAttr } = deps;
  const scenario = panel._scenarioEditor;
  const overlay = el("div", "scenario-editor-overlay");
  const dialog = el("section", "scenario-editor-dialog");
  setAttr(dialog, "role", "dialog");
  setAttr(dialog, "aria-modal", "true");
  const header = el("header", "scenario-editor-header");
  const title = el("div");
  title.appendChild(el("span", "scenario-editor-kicker", "РЕДАКТОР СЦЕНАРИЯ"));
  title.appendChild(el("h2", null, scenario.title || "Новый сценарий"));
  title.appendChild(el("p", null, "Настройте запуск, условия и последовательность действий."));
  header.appendChild(title);
  const close = el("button", "secondary scenario-editor-close");
  close.type = "button";
  close.textContent = "×";
  setAttr(close, "aria-label", "Закрыть редактор");
  close.addEventListener("click", () => { panel._scenarioEditor = null; updateScenarioEditor(panel); });
  header.appendChild(close);
  dialog.appendChild(header);

  const body = el("div", "scenario-editor-body");
  const about = scenarioEditorSection(deps, "О сценарии", "Название и визуальное обозначение на панели и планшете.");
  const aboutGrid = el("div", "scenario-editor-grid");
  aboutGrid.appendChild(scenarioField(deps, "Название", scenario.title, (value) => { scenario.title = value; }, { placeholder: "Например: Доброе утро", maxlength: 120 }));
  aboutGrid.appendChild(scenarioSelectField(deps, "Иконка", scenario.icon, ICONS, (value) => { scenario.icon = value; }));
  aboutGrid.appendChild(scenarioField(deps, "Группа", scenario.group, (value) => { scenario.group = value; }, { placeholder: "Сценарии" }));
  aboutGrid.appendChild(scenarioField(deps, "Описание", scenario.description, (value) => { scenario.description = value; }, { multiline: true, wide: true, maxlength: 500 }));
  about.appendChild(aboutGrid);
  body.appendChild(about);

  const execution = scenarioEditorSection(deps, "Выполнение", "Определяет поведение при повторном запуске сценария.");
  execution.appendChild(scenarioSelectField(deps, "Повторный запуск", scenario.definition.executionMode, [
    ["single", "Один запуск — повтор игнорируется"], ["restart", "Перезапуск — начать заново"], ["queued", "Очередь — выполнить последовательно"],
  ], (value) => { scenario.definition.executionMode = value; }));
  body.appendChild(execution);

  const publication = scenarioEditorSection(deps, "Публикация", "Управляет доступностью и быстрым доступом на главном экране.");
  publication.appendChild(scenarioToggle(deps, "Сценарий включён", "Разрешить автоматический и ручной запуск", scenario.enabled, (value) => { scenario.enabled = value; }));
  publication.appendChild(scenarioToggle(deps, "В быстром доступе", "Показывать сценарий среди избранных", scenario.favorite, (value) => { scenario.favorite = value; }));
  publication.appendChild(scenarioToggle(deps, "Требовать подтверждение", "Перед запуском опасного действия запросить подтверждение", scenario.requiresConfirmation, (value) => { scenario.requiresConfirmation = value; }));
  body.appendChild(publication);

  const rules = [
    ["trigger", "Когда запускать", "Можно выбрать ручной запуск, время, присутствие или изменение устройства.", scenario.definition.triggers],
    ["condition", "Дополнительные условия", "Все заданные условия должны выполняться. Раздел можно оставить пустым.", scenario.definition.conditions],
    ["action", "Что выполнить", "Действия выполняются сверху вниз с подтверждением результата устройства.", scenario.definition.actions],
  ];
  rules.forEach(([kind, heading, description, items]) => {
    const section = scenarioEditorSection(deps, heading, description);
    const list = el("div", "scenario-rule-list");
    items.forEach((item, index) => list.appendChild(scenarioRuleCard(panel, kind, item, index, items, deps)));
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
    body.appendChild(section);
  });
  dialog.appendChild(body);

  const footer = el("footer", "scenario-editor-footer");
  const issues = scenarioEditorIssues(scenario);
  footer.appendChild(el("div", issues.length ? "scenario-editor-status is-warning" : "scenario-editor-status is-ready", issues[0] || "Сценарий готов к сохранению"));
  const buttons = el("div", "scenario-editor-footer-actions");
  const test = el("button", "secondary", "Проверить");
  test.disabled = panel._busy;
  test.addEventListener("click", () => submitScenario(panel, deps, true));
  const cancel = el("button", "secondary", "Отмена");
  cancel.addEventListener("click", () => { panel._scenarioEditor = null; updateScenarioEditor(panel); });
  const save = el("button", null, panel._busy ? "Сохранение…" : "Сохранить");
  save.disabled = panel._busy;
  save.addEventListener("click", () => submitScenario(panel, deps, false));
  buttons.appendChild(test); buttons.appendChild(cancel); buttons.appendChild(save);
  footer.appendChild(buttons);
  dialog.appendChild(footer);
  overlay.appendChild(dialog);
  container.appendChild(overlay);
}

export function renderScenarioSection(panel, container, deps) {
  const { el, svgIcon } = deps;
  container.innerHTML = "";
  const card = el("div", "card scenarios-card");
  const heading = el("div", "scenarios-heading");
  const headingCopy = el("div");
  headingCopy.appendChild(el("h2", null, "Сценарии"));
  headingCopy.appendChild(el("p", "section-intro", "Создавайте, запускайте и изменяйте сценарии дома"));
  heading.appendChild(headingCopy);
  const create = el("button", "scenario-create", "+ Создать сценарий");
  create.type = "button";
  create.addEventListener("click", () => { panel._scenarioEditor = defaultScenarioDraft(); updateScenarioEditor(panel); });
  heading.appendChild(create);
  card.appendChild(heading);
  if (panel._scenarios.loading && !panel._scenarios.list) {
    card.appendChild(el("div", "muted", "Загрузка сценариев…"));
  } else if (!panel._scenarios.list || !Array.isArray(panel._scenarios.list.scenarios)) {
    card.appendChild(el("div", "muted", "Список сценариев недоступен."));
  } else {
    const items = panel._scenarios.list.scenarios;
    if (!items.length) {
      const empty = el("div", "scenario-empty");
      const icon = el("span", "scenario-empty-icon");
      icon.appendChild(svgIcon("bolt"));
      empty.appendChild(icon);
      empty.appendChild(el("h3", null, "Создайте первый сценарий"));
      empty.appendChild(el("p", null, "Объедините команды устройств, расписание и условия в одно понятное действие."));
      const emptyCreate = el("button", "secondary", "Создать сценарий");
      emptyCreate.addEventListener("click", () => { panel._scenarioEditor = defaultScenarioDraft(); updateScenarioEditor(panel); });
      empty.appendChild(emptyCreate);
      card.appendChild(empty);
    }
    const list = el("div", "scenario-list");
    items.forEach((scenario) => {
      const requiresConfirmation = scenario.requiresConfirmation === true || scenario.requires_confirmation === true;
      const row = el("article", `scenario-row${scenario.enabled ? "" : " is-disabled"}`);
      const icon = el("span", "scenario-icon");
      icon.appendChild(svgIcon(panel._scenarioIconName(scenario)));
      row.appendChild(icon);
      const copy = el("div", "scenario-copy");
      copy.tabIndex = 0;
      copy.appendChild(el("h3", null, scenario.title || scenario.id));
      copy.appendChild(el("p", "muted", [scenarioSummary(normalizedScenario(scenario)), requiresConfirmation ? "требуется подтверждение" : "", scenario.enabled ? "" : "выключен"].filter(Boolean).join(" · ")));
      copy.addEventListener("click", () => { panel._scenarioEditor = normalizedScenario(scenario); updateScenarioEditor(panel); });
      copy.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          panel._scenarioEditor = normalizedScenario(scenario);
          updateScenarioEditor(panel);
        }
      });
      row.appendChild(copy);
      const actions = el("div", "scenario-actions");
      if (scenario.enabled) {
        const run = el("button", null, "Запустить");
        run.disabled = panel._busy;
        run.addEventListener("click", () => panel._post(deps.runApi, { scenario_id: scenario.id }, requiresConfirmation ? `Запустить сценарий "${scenario.title}"?` : null));
        actions.appendChild(run);
      }
      const test = el("button", "secondary", "Проверить");
      test.disabled = panel._busy;
      test.addEventListener("click", () => panel._scenarioTest(scenario));
      actions.appendChild(test);
      const remove = el("button", "secondary", "Удалить");
      remove.disabled = panel._busy;
      remove.addEventListener("click", () => panel._post(deps.deleteApi, { scenario_id: scenario.id }, `Удалить сценарий "${scenario.title}"?`));
      actions.appendChild(remove);
      row.appendChild(actions);
      list.appendChild(row);
    });
    card.appendChild(list);
  }
  container.appendChild(card);
  if (panel._scenarioEditor) renderScenarioEditor(panel, container, deps);
}
