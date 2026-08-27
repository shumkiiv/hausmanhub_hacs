import { activeElementWithin, trapModalTabKey } from "./hausman-hub-modal.js?v=1.52.182";
import { scenarioEditorIssues, scenarioEventFields, scenarioField, scenarioIconField, scenarioSelectField, scenarioToggle } from "./hausman-hub-scenario-fields.js?v=1.52.182";
import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.182";
import { scenarioCapabilityLabel, scenarioDeviceButton, scenarioDeviceFields, scenarioGroupForTarget, scenarioPhysicalGroups } from "./hausman-hub-scenario-device-picker.js?v=1.52.182";
import { groupScenarios, renderScenarioCatalog, scenarioActivationKind, scenarioDisplayGroup, scenarioDisplayText } from "./hausman-hub-scenario-catalog.js?v=1.52.182";
import { renderScenarioRoomPicker, scenarioAffectedDeviceCount, scenarioRoomLabels } from "./hausman-hub-scenario-rooms.js?v=1.52.182";
import { defaultScenarioDraft, duplicateScenarioDraft, normalizedScenario, scenarioPayload } from "./hausman-hub-scenario-state.js?v=1.52.182";
import { bulkSaveScenarios } from "./hausman-hub-scenario-bulk.js?v=1.52.182";
import { openScenarioAiComposer, renderScenarioAiComposer } from "./hausman-hub-scenario-ai.js?v=1.52.182";
import { closeManagedSourceEditor, openManagedSourceEditor, renderManagedSourceEditor } from "./hausman-hub-scenario-node-red.js?v=1.52.182";

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

function duplicateScenarioWithChoices(source) {
  const keepRooms = window.confirm("Сохранить комнаты исходного сценария в копии?");
  const keepActions = window.confirm("Сохранить действия исходного сценария в копии?");
  return duplicateScenarioDraft(source, { keepRooms, keepActions });
}

function openScenarioEditor(panel, source) {
  panel._scenarioEditor = localizeSystemScenarioDraft(normalizedScenario(source));
  panel._scenarioEditorOriginal = JSON.stringify(scenarioPayload(panel._scenarioEditor));
  panel._scenarioEditorExpanded = { trigger: 0, condition: 0, action: 0 };
  panel._scenarioEditorRestoreFocus = activeElementWithin(panel);
  panel._scenarioEditorJustOpened = true;
  panel._scenarioDryRun = null;
  updateScenarioEditor(panel);
}

function scenarioEditorDirty(panel) {
  if (!panel._scenarioEditor) return false;
  return JSON.stringify(scenarioPayload(panel._scenarioEditor)) !== panel._scenarioEditorOriginal;
}

function closeScenarioEditor(panel) {
  if (panel._scenarioNodeRedEditor) {
    closeManagedSourceEditor(panel, updateScenarioEditor);
    if (panel._scenarioNodeRedEditor) return false;
  }
  if (scenarioEditorDirty(panel) && !window.confirm("Закрыть редактор без сохранения изменений?")) return false;
  panel._scenarioEditor = null;
  panel._scenarioEditorOriginal = null;
  panel._scenarioEditorExpanded = null;
  panel._scenarioEditorJustOpened = false;
  const restore = panel._scenarioEditorRestoreFocus;
  panel._scenarioEditorRestoreFocus = null;
  updateScenarioEditor(panel);
  if (restore && typeof restore.focus === "function" && restore.isConnected !== false) {
    try { restore.focus(); } catch (error) { /* opener removed */ }
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

function renderExecutionBackend(panel, scenario, deps) {
  const { el, setAttr } = deps;
  const section = scenarioEditorSection(deps, "Где выполняется алгоритм");
  const status = panel._scenarios.nodeRed || {};
  const backend = scenario.definition.executionBackend || "hausman";
  const choices = el("div", "scenario-backend-choices");
  [
    ["hausman", "Hausman", "Быстрые обычные сценарии без внешней зависимости.", true],
    ["node_red", "Node-RED", "Компактная function-схема для ветвлений. Команды всё равно проверяет Hausman.", status.available === true],
  ].forEach(([value, title, help, available]) => {
    const button = el("button", `scenario-backend-choice${backend === value ? " is-selected" : ""}`);
    button.type = "button";
    button.disabled = !available;
    setAttr(button, "aria-pressed", backend === value ? "true" : "false");
    button.appendChild(el("b", null, title));
    button.appendChild(el("small", null, help));
    button.addEventListener("click", () => {
      scenario.definition.executionBackend = value;
      if (value === "node_red") {
        scenario.definition.nodeRed = scenario.definition.nodeRed || {
          generatedBy: "hausman", syncStatus: "pending", inputTargetIds: [],
        };
      } else delete scenario.definition.nodeRed;
      panel._scenarioDryRun = null;
      updateScenarioEditor(panel);
    });
    choices.appendChild(button);
  });
  section.appendChild(choices);
  const statusText = status.available
    ? `Node-RED ${status.version || ""} подключён. Управляемый flow будет синхронизирован при сохранении.`
    : status.message || "Node-RED не установлен или не отвечает. Выберите выполнение в Hausman.";
  section.appendChild(el("p", `scenario-backend-status${status.available ? " is-ready" : " is-warning"}`, statusText));

  if (backend === "node_red") {
    const metadata = scenario.definition.nodeRed || { inputTargetIds: [] };
    scenario.definition.nodeRed = metadata;
    const field = el("label", "scenario-field scenario-node-red-inputs");
    field.appendChild(el("span", null, "Данные для алгоритма"));
    const select = el("select");
    select.multiple = true;
    setAttr(select, "aria-label", "Устройства и датчики, доступные функции Node-RED");
    const selected = new Set(metadata.inputTargetIds || []);
    scenarioDevices(panel).forEach((device) => {
      const option = el("option", null, device.physical_name || device.name || device.target_id);
      option.value = device.target_id;
      option.selected = selected.has(device.target_id);
      select.appendChild(option);
    });
    select.addEventListener("change", () => {
      metadata.inputTargetIds = Array.from(select.selectedOptions).map((option) => option.value).slice(0, 32);
    });
    field.appendChild(select);
    field.appendChild(el("small", null, "Выберите датчики, значения которых нужны для ветвлений. Устройства из триггеров и действий добавятся автоматически."));
    section.appendChild(field);
    if (metadata.syncStatus) {
      section.appendChild(el("span", `scenario-editor-badge scenario-node-red-sync is-${metadata.syncStatus}`, {
        synced: "Flow синхронизирован", changed: "Функция изменена вручную", missing: "Flow не найден",
        pending: "Flow будет создан", unavailable: "Node-RED недоступен",
      }[metadata.syncStatus] || metadata.syncStatus));
    }
    const managed = Array.isArray(status.flows)
      ? status.flows.find((item) => item.flowId === metadata.flowId) : null;
    if (metadata.flowId && ((managed && managed.sourcePath) || status.available)) {
      const edit = el("button", "secondary scenario-node-red-edit", "Редактировать алгоритм в Hausman");
      edit.type = "button";
      edit.addEventListener("click", () => openManagedSourceEditor(panel, scenario, deps, updateScenarioEditor));
      section.appendChild(edit);
    }
    if (managed && managed.openPath) {
      const link = el("a", "scenario-node-red-open", "Открыть function-схему в Node-RED");
      link.href = managed.openPath;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      section.appendChild(link);
    }
  }
  return section;
}

function renderScenarioTrace(panel, deps) {
  const report = panel._scenarioDryRun;
  if (!report) return null;
  const { el } = deps;
  const section = el("section", "scenario-trace");
  section.appendChild(el("h3", null, "Отладка пробного запуска"));
  section.appendChild(el("p", null, report.summary || "Проверка завершена без отправки команд."));
  const nodeRed = report.nodeRed;
  if (nodeRed && Array.isArray(nodeRed.trace)) {
    const meta = el("p", "scenario-trace-meta", `Ветка: ${nodeRed.selectedBranch || "не выбрана"} · расчёт ${nodeRed.durationMs || 0} мс`);
    section.appendChild(meta);
    const list = el("ol", "scenario-trace-list");
    nodeRed.trace.forEach((item) => {
      const row = el("li", `is-${item.status || "skipped"}`);
      row.appendChild(el("b", null, item.title || item.id || "Проверка"));
      const evidence = [
        item.actual !== null && item.actual !== undefined ? `факт: ${item.actual}` : "",
        item.expected !== null && item.expected !== undefined ? `ожидалось: ${item.expected}` : "",
        item.reason || "",
      ].filter(Boolean).join(" · ");
      if (evidence) row.appendChild(el("small", null, evidence));
      list.appendChild(row);
    });
    section.appendChild(list);
  }
  return section;
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
  deps.setAttr(section, "data-scenario-step", kind);
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

async function submitScenario(panel, deps, testOnly) {
  const scenario = panel._scenarioEditor;
  const issues = scenarioEditorIssues(scenario);
  if (issues.length) {
    focusScenarioProblem(panel, issues[0]);
    updateScenarioEditor(panel);
    return;
  }
  if (panel._busy) return;
  panel._busy = true;
  panel._notice = "";
  panel._error = false;
  updateScenarioEditor(panel);
  try {
    const response = await panel._hass.callApi("POST", testOnly ? deps.testApi : deps.scenariosApi, scenarioPayload(scenario));
    if (testOnly) {
      panel._scenarioDryRun = response && response.report ? response.report : null;
      panel._notice = `Сценарий «${scenario.title}» прошёл проверку.`;
    } else {
      panel._scenarioEditor = null;
      panel._scenarioEditorOriginal = null;
      panel._scenarioEditorExpanded = null;
      panel._scenarios.list = null;
      panel._notice = `Сценарий «${scenario.title}» сохранён.`;
    }
  } catch (error) {
    const body = error && typeof error.body === "object" ? error.body : {};
    const violations = Array.isArray(body.violations) ? body.violations : [];
    const firstViolation = violations[0] || null;
    if (firstViolation) focusScenarioProblem(panel, firstViolation);
    if (["missing_room", "missing_device", "missing_action"].includes(firstViolation && firstViolation.code)) {
      try {
        panel._scenarios.catalog = await panel._hass.callApi("GET", deps.catalogApi);
      } catch (catalogError) { /* keep the last complete catalog */ }
    }
    panel._error = true;
    if (body.error === "revision_conflict") {
      const changed = Array.isArray(body.changedFields) ? body.changedFields.join(", ") : "содержимое";
      panel._notice = `Сценарий уже изменён на другом устройстве: ${changed}. Перечитайте его перед сохранением.`;
    } else {
      panel._notice = firstViolation && firstViolation.message
        ? `${firstViolation.message} Каталог устройств перечитан.`
        : testOnly ? "Проверка не пройдена. Исправьте отмеченные поля." : "Сценарий не сохранён. Проверьте заполнение действий.";
    }
  } finally {
    panel._busy = false;
  }
  if (!testOnly && !panel._error) await panel._loadScenarios();
  else updateScenarioEditor(panel);
}

function focusScenarioProblem(panel, issue) {
  const path = String(issue && (issue.path || issue.step) || "");
  const kind = path.includes("action") ? "action" : path.includes("condition") ? "condition" : path.includes("trigger") ? "trigger" : "about";
  if (["action", "condition", "trigger"].includes(kind)) panel._scenarioEditorExpanded[kind] = 0;
  panel._scenarioEditorProblemStep = kind;
  Promise.resolve().then(() => {
    const target = panel._shell && panel._shell.scenarios
      && panel._shell.scenarios.querySelector(`[data-scenario-step="${kind}"]`);
    if (target && typeof target.scrollIntoView === "function") target.scrollIntoView({ block: "nearest" });
    const focusable = target && target.querySelector("input,select,textarea,button");
    if (focusable && typeof focusable.focus === "function") focusable.focus();
  });
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

function scenarioReviewDetails(panel, scenario, deps) {
  const review = deps.el("div", "scenario-editor-change-preview");
  review.appendChild(deps.el("strong", null, "Что изменится после сохранения"));
  const rooms = scenarioRoomLabels(panel, scenario).join(", ");
  const devices = scenarioAffectedDeviceCount(panel, scenario);
  const list = deps.el("ul");
  list.appendChild(deps.el("li", null, `Комнаты: ${rooms}`));
  list.appendChild(deps.el("li", null, `Физических устройств: ${devices}`));
  list.appendChild(deps.el("li", null, `Действий: ${scenario.definition.actions.length}; триггеров: ${scenario.definition.triggers.length}; условий: ${scenario.definition.conditions.length}`));
  list.appendChild(deps.el("li", null, scenario.enabled ? "Сценарий будет включён" : "Сценарий останется выключенным"));
  review.appendChild(list);
  return review;
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
  setAttr(left, "data-scenario-step", "about");
  const about = scenarioEditorSection(deps, "О сценарии");
  const grid = el("div", "scenario-editor-grid is-single");
  grid.appendChild(scenarioField(deps, "Название", scenario.title, (value) => { scenario.title = value; }, { placeholder: "Например: Доброе утро", maxlength: 120 }));
  grid.appendChild(scenarioField(deps, "Группа", scenario.group, (value) => { scenario.group = value; }, { placeholder: "Сценарии" }));
  grid.appendChild(renderScenarioRoomPicker(panel, scenario, deps, () => updateScenarioEditor(panel)));
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
  left.appendChild(renderExecutionBackend(panel, scenario, deps));
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
  setAttr(right, "data-scenario-step", "action");
  right.appendChild(renderScenarioRules(panel, "action", "Выполнить", "Шаги идут строго сверху вниз", scenario.definition.actions, deps));
  workspace.appendChild(right);
  dialog.appendChild(workspace);

  const footer = el("footer", "scenario-editor-footer");
  const trace = renderScenarioTrace(panel, deps);
  if (trace) footer.appendChild(trace);
  footer.appendChild(scenarioReviewDetails(panel, scenario, deps));
  footer.appendChild(el("p", "scenario-editor-review-summary", scenarioReviewSummary(scenario)));
  const footerRow = el("div", "scenario-editor-footer-row");
  footerRow.appendChild(el("div", issues.length ? "scenario-editor-status is-warning" : "scenario-editor-status is-ready",
    issues.length ? `${issues[0].message}${issues.length > 1 ? ` · ещё ${issues.length - 1}` : ""}` : "Сценарий готов к сохранению"));
  const buttons = el("div", "scenario-editor-footer-actions");
  const needsNodeRedSave = scenario.definition.executionBackend === "node_red"
    && !(scenario.definition.nodeRed && scenario.definition.nodeRed.flowId);
  const test = el("button", "secondary", needsNodeRedSave ? "Сначала сохранить" : "Пробный запуск");
  test.disabled = panel._busy || issues.length > 0 || needsNodeRedSave;
  if (needsNodeRedSave) test.title = "Hausman создаст управляемую function-схему при первом сохранении";
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
  panel._scenarioLibrary = panel._scenarioLibrary || { filter: "all", roomId: "all", query: "", selectedIds: new Set(), bulkRoomIds: new Set() };
  if (!panel._scenarioLibrary.roomId) panel._scenarioLibrary.roomId = "all";
  const items = panel._scenarios.list && Array.isArray(panel._scenarios.list.scenarios)
    ? panel._scenarios.list.scenarios : [];
  const logicalGroups = groupScenarios(items);

  container.appendChild(createLibraryHero(panel, {
    eyebrow: "СЦЕНАРИИ ДОМА",
    title: "Дом работает по вашим правилам",
    subtitle: `${items.length} сценариев · ${items.filter((item) => item.favorite === true).length} на главной · ${items.filter((item) => item.enabled === false).length} отключено`,
    facts: logicalGroups.map((group) => ({ label: group.title, value: group.scenarios.length })),
  }, deps));

  const card = el("section", "card scenarios-card scenario-library");
  const heading = el("div", "scenarios-heading scenario-library-toolbar");
  const headingCopy = el("div");
  headingCopy.appendChild(el("h2", null, "Сценарии"));
  headingCopy.appendChild(el("p", "section-intro scenario-library-count", `Показано ${items.length} из ${items.length}`));
  heading.appendChild(headingCopy);
  const create = el("button", "scenario-create", "Новый сценарий");
  create.type = "button";
  create.addEventListener("click", () => openScenarioEditor(panel, defaultScenarioDraft()));
  const createActions = el("div", "scenario-create-actions");
  const createAi = el("button", "secondary scenario-create-ai", "Создать с Hausman AI");
  createAi.type = "button";
  createAi.addEventListener("click", () => openScenarioAiComposer(panel, () => updateScenarioEditor(panel)));
  createActions.appendChild(createAi);
  createActions.appendChild(create);
  heading.appendChild(createActions);
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
      duplicate: duplicateScenarioWithChoices,
      save: (scenario, notice) => saveScenarioQuick(panel, deps, scenario, notice),
      delete: (scenario) => deleteScenarioQuick(panel, deps, scenario),
      refresh: () => updateScenarioEditor(panel),
      bulkSave: (scenarios, notice) => bulkSaveScenarios(panel, deps, scenarios, notice),
      isSelected: (id) => panel._scenarioLibrary.selectedIds.has(id),
      toggleSelected: (id, selected) => {
        if (selected) panel._scenarioLibrary.selectedIds.add(id);
        else panel._scenarioLibrary.selectedIds.delete(id);
        updateScenarioEditor(panel);
      },
    });
  }
  container.appendChild(card);
  renderScenarioAiComposer(
    panel,
    container,
    deps,
    (draft) => openScenarioEditor(panel, draft),
    () => updateScenarioEditor(panel),
  );
  if (panel._scenarioEditor) renderScenarioEditor(panel, container, deps);
  if (panel._scenarioNodeRedEditor) renderManagedSourceEditor(panel, container, deps, updateScenarioEditor);
}
