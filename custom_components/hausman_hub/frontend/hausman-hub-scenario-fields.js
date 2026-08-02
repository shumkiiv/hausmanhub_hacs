/* Focused form controls for the guided scenario editor. */

import { SCENARIO_ICON_GROUPS, scenarioIconMeta } from "./hausman-hub-scenario-icons.js?v=1.51.65";

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
