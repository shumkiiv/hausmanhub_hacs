/* Focused form controls for the guided scenario editor. */

import { SCENARIO_ICON_GROUPS, scenarioIconMeta } from "./hausman-hub-scenario-icons.js?v=1.52.83";

export function scenarioField(deps, label, value, onChange, options = {}) {
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

export function scenarioEventFields(deps, rule, onChange) {
  const fragment = deps.el("div", "scenario-rule-fields");
  fragment.appendChild(scenarioField(deps, "Тип события", rule.eventType || "", (eventType) => onChange({ ...rule, eventType }), {
    placeholder: "например zha_event", maxlength: 64,
    help: "Только точный custom event. Системные события Home Assistant недоступны.",
  }));
  const filter = rule.eventDataText ?? (rule.eventData && Object.keys(rule.eventData).length ? JSON.stringify(rule.eventData, null, 2) : "");
  fragment.appendChild(scenarioField(deps, "Фильтр данных (необязательно)", filter, (eventDataText) => onChange({ ...rule, eventDataText }), {
    multiline: true, wide: true, maxlength: 1200, placeholder: '{"device_id":"button-kids","action":"single"}',
    help: "JSON-объект: до 12 строковых, числовых или логических значений. Пустое поле принимает любое событие этого типа.",
  }));
  return fragment;
}

export function scenarioSelectField(deps, label, value, options, onChange, help = "") {
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

export function scenarioIconField(deps, value, onChange) {
  const { el, setAttr } = deps;
  const field = el("div", "scenario-editor-field scenario-icon-field");
  field.appendChild(el("span", null, "Иконка"));
  const picker = el("details", "scenario-icon-picker");
  const current = scenarioIconMeta(value);
  const summary = el("summary", "scenario-icon-current");
  const currentIcon = el("ha-icon", "icon scenario-material-icon");
  setAttr(currentIcon, "icon", `mdi:${current.mdi}`);
  summary.appendChild(currentIcon);
  const currentLabel = el("strong", null, current.label);
  summary.appendChild(currentLabel);
  summary.appendChild(el("small", null, "Выбрать из коллекции"));
  picker.appendChild(summary);
  const catalog = el("div", "scenario-icon-catalog");
  SCENARIO_ICON_GROUPS.forEach((group) => {
    catalog.appendChild(el("h4", null, group.title));
    const grid = el("div", "scenario-icon-grid");
    group.items.forEach((item) => {
      const button = el("button", item.key === current.key ? "is-selected" : "");
      button.type = "button";
      setAttr(button, "aria-label", `Выбрать иконку «${item.label}»`);
      const itemIcon = el("ha-icon", "icon scenario-material-icon");
      setAttr(itemIcon, "icon", `mdi:${item.mdi}`);
      button.appendChild(itemIcon);
      button.appendChild(el("span", null, item.label));
      button.addEventListener("click", () => {
        onChange(`mdi:${item.key}`);
        setAttr(currentIcon, "icon", `mdi:${item.mdi}`);
        currentLabel.textContent = item.label;
        picker.open = false;
      });
      grid.appendChild(button);
    });
    catalog.appendChild(grid);
  });
  picker.appendChild(catalog);
  field.appendChild(picker);
  field.appendChild(el("small", null, "Ассоциативные иконки сгруппированы по назначению."));
  return field;
}

export function scenarioToggle(deps, label, description, checked, onChange) {
  const { el, setAttr } = deps;
  let state = checked === true;
  const wrapper = el("button", `scenario-editor-toggle${state ? " is-on" : ""}`);
  wrapper.type = "button";
  setAttr(wrapper, "role", "switch");
  setAttr(wrapper, "aria-checked", state ? "true" : "false");
  const copy = el("span");
  copy.appendChild(el("b", null, label));
  copy.appendChild(el("small", null, description));
  wrapper.appendChild(copy);
  const track = el("span", "scenario-editor-switch-track");
  track.appendChild(el("span", "scenario-editor-switch-knob"));
  wrapper.appendChild(track);
  wrapper.addEventListener("click", () => {
    state = !state;
    setAttr(wrapper, "aria-checked", state ? "true" : "false");
    wrapper.classList.toggle("is-on", state);
    onChange(state);
  });
  return wrapper;
}

export function scenarioEditorIssues(scenario) {
  const issues = [];
  const add = (code, step, message) => issues.push({ code, step, message });
  if (!String(scenario.title || "").trim()) add("title_required", "about", "Укажите название сценария.");
  if (!scenario.definition.triggers.length) add("trigger_required", "triggers", "Добавьте хотя бы один триггер.");
  if (!scenario.definition.actions.length) add("action_required", "actions", "Добавьте хотя бы одно действие.");
  scenario.definition.triggers.forEach((trigger) => {
    if (trigger.type === "time" && !/^([01]\d|2[0-3]):[0-5]\d$/.test(String(trigger.value || ""))) add("trigger_time_invalid", "triggers", "Укажите корректное время запуска.");
    if (trigger.type === "device_state" && (!trigger.targetId || !trigger.property || !trigger.comparison)) add("trigger_device_incomplete", "triggers", "Для триггера выберите устройство, показатель и сравнение.");
    if (trigger.type === "device_state" && trigger.comparison !== "changed" && String(trigger.value ?? "").trim() === "") add("trigger_value_required", "triggers", "Укажите значение, которое запустит сценарий.");
    if (trigger.type === "event") {
      const eventType = String(trigger.eventType || "").trim();
      if (!/^[a-z][a-z0-9_]{1,63}$/.test(eventType)
          || ["state_changed", "call_service", "service_executed", "homeassistant_start", "homeassistant_stop"].includes(eventType)) {
        add("trigger_event_type_invalid", "triggers", "Укажите разрешённый тип внешнего события.");
      }
      const filter = eventDataFromDraft(trigger);
      if (filter.error) add("trigger_event_filter_invalid", "triggers", filter.error);
    }
  });
  scenario.definition.conditions.forEach((condition) => {
    if (condition.type === "time_window" && !/^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$/.test(String(condition.value || ""))) add("condition_window_invalid", "conditions", "Укажите временной промежуток в формате 22:00-07:00.");
    if (condition.type === "weekday" && !String(condition.value || "").trim()) add("condition_weekday_required", "conditions", "Выберите хотя бы один день недели.");
    if (condition.type === "device_state" && (!condition.targetId || !condition.property || !condition.comparison)) add("condition_device_incomplete", "conditions", "Для условия выберите устройство, показатель и сравнение.");
    if (condition.type === "device_state" && condition.comparison !== "changed" && String(condition.value ?? "").trim() === "") add("condition_value_required", "conditions", "Укажите значение для проверки условия.");
  });
  scenario.definition.actions.forEach((action) => {
    if (action.type === "device_action" && (!action.targetId || !action.actionId)) add("device_action_incomplete", "actions", "Для каждого действия выберите устройство и команду.");
    if (action.type === "run_scenario" && !action.scenarioId) add("scenario_action_incomplete", "actions", "Выберите запускаемый сценарий.");
    if (action.type === "notification" && !String(action.message || "").trim()) add("notification_empty", "actions", "Введите текст уведомления.");
    if (action.type === "delay" && (!Number.isFinite(Number(action.delaySeconds)) || Number(action.delaySeconds) < 1)) add("delay_invalid", "actions", "Пауза должна быть не меньше одной секунды.");
  });
  return [...new Map(issues.map((issue) => [issue.code, issue])).values()];
}

export function eventDataFromDraft(trigger) {
  const raw = String(trigger.eventDataText ?? "").trim();
  if (!raw) return { value: trigger.eventData || {} };
  try {
    const value = JSON.parse(raw);
    if (!value || Array.isArray(value) || typeof value !== "object" || Object.keys(value).length > 12) throw new Error();
    for (const [key, item] of Object.entries(value)) {
      if (!/^[a-z][a-z0-9_-]{0,63}$/.test(key)
          || !["string", "number", "boolean"].includes(typeof item)
          || (typeof item === "number" && !Number.isFinite(item))) throw new Error();
    }
    return { value };
  } catch (error) {
    return { error: "Фильтр события должен быть JSON-объектом максимум с 12 простыми значениями." };
  }
}
