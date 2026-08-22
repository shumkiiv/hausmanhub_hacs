/* Tablet-style progress feedback for commands started from the HACS panel. */

const COMMAND_INTENT_TTL_MS = 2500;

function compactText(value) {
  return String(value || "").replace(/\s+/gu, " ").trim();
}

function attribute(node, name) {
  if (!node) return "";
  if (typeof node.getAttribute === "function") return node.getAttribute(name) || "";
  return node.attributes && node.attributes[name] ? node.attributes[name] : node[name] || "";
}

function controlLabel(node) {
  return compactText(attribute(node, "aria-label") || attribute(node, "title") || node?.textContent);
}

function isControl(node) {
  if (!node || typeof node !== "object") return false;
  const tag = String(node.tagName || "").toLocaleLowerCase("ru");
  return ["button", "input", "select"].includes(tag)
    || attribute(node, "role") === "button";
}

function activeLabel(intent, now = Date.now()) {
  if (!intent || !intent.label) return "";
  if (intent.active || now - intent.startedAt <= COMMAND_INTENT_TTL_MS) {
    intent.active = true;
    return intent.label;
  }
  return "";
}

function controlsIn(root) {
  const controls = [];
  const visit = (node) => {
    if (!node) return;
    if (isControl(node)) controls.push(node);
    Array.from(node.children || []).forEach(visit);
  };
  visit(root);
  return controls;
}

export function captureCommandIntent(panel, event, now = Date.now()) {
  const path = typeof event?.composedPath === "function" ? event.composedPath() : [];
  const control = path.find(isControl) || (isControl(event?.target) ? event.target : null);
  if (!control || control.disabled || attribute(control, "aria-disabled") === "true") return null;
  const label = controlLabel(control);
  if (!label) return null;
  const intent = { label, control, startedAt: now, active: false };
  panel._commandIntent = intent;
  return intent;
}

export function applyCommandActivity(element, detail, busy, intent, setAttr, now = Date.now()) {
  setAttr(element, "aria-busy", busy ? "true" : "false");
  if (!busy) {
    element.style.display = "none";
    return "";
  }
  const label = activeLabel(intent, now);
  detail.textContent = label || "Отправляем команду и ждём подтверждение";
  element.style.display = "";
  return label;
}

export function applyCommandTarget(panel, root, busy, intent, setAttr, now = Date.now()) {
  const previous = panel._commandPendingTarget;
  if (previous) {
    previous.classList?.remove("is-command-pending");
    if (typeof previous.removeAttribute === "function") previous.removeAttribute("aria-busy");
    else setAttr(previous, "aria-busy", "false");
  }
  panel._commandPendingSpinner?.remove?.();
  panel._commandPendingTarget = null;
  panel._commandPendingSpinner = null;
  if (!busy) return null;

  const label = activeLabel(intent, now);
  if (!label) return null;
  const controls = controlsIn(root);
  const normalized = label.toLocaleLowerCase("ru");
  const target = controls.includes(intent.control)
    ? intent.control
    : controls.find((control) => controlLabel(control).toLocaleLowerCase("ru") === normalized);
  if (!target) return null;

  target.classList?.add("is-command-pending");
  setAttr(target, "aria-busy", "true");
  const tag = String(target.tagName || "").toLocaleLowerCase("ru");
  if (["input", "select"].includes(tag)) {
    panel._commandPendingTarget = target;
    return target;
  }
  const spinner = document.createElement("span");
  spinner.className = "command-target-spinner";
  setAttr(spinner, "aria-hidden", "true");
  target.appendChild(spinner);
  panel._commandPendingTarget = target;
  panel._commandPendingSpinner = spinner;
  return target;
}
