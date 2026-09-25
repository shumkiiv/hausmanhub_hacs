/* Shared, non-layout-shifting feedback policy for every panel operation. */

export function feedbackTone(message) {
  const text = String(message || "").trim().toLocaleLowerCase("ru");
  if (!text) return "info";
  if (/(не выполн|не удалось|не пройден|не ответил|не найден|недоступ|неизвест|ошиб|отклонен|отклонён|не сохран)/u.test(text)) return "error";
  if (/(частично|еще провер|ещё провер|подтверждение|ожида|введите|проверьте|изменил|не опубликован|повторите)/u.test(text)) return "warning";
  return "success";
}

export function applyFeedback(element, message, setAttr) {
  const text = message || "";
  if (element._feedbackMessage !== text) {
    if (element._feedbackTimer) clearTimeout(element._feedbackTimer);
    element._feedbackMessage = text;
    element._feedbackTimer = 0;
    element._feedbackDismissed = false;
  }
  element.textContent = text;
  element.className = "notice";
  if (!text) {
    element.style.display = "none";
    return "info";
  }
  const tone = feedbackTone(text);
  element.className = `notice is-${tone}`;
  setAttr(element, "role", tone === "error" ? "alert" : "status");
  setAttr(element, "aria-live", tone === "error" ? "assertive" : "polite");
  element.style.display = element._feedbackDismissed ? "none" : "";
  if (tone === "success" && !element._feedbackDismissed && !element._feedbackTimer) {
    element._feedbackTimer = setTimeout(() => {
      element._feedbackTimer = 0;
      element._feedbackDismissed = true;
      element.style.display = "none";
    }, 4500);
  }
  return tone;
}

const CRITICAL_TEXT = {
  light: "Свет", climate: "Климат", unavailable: "недоступен",
  stale: "устаревшие показания", unhealthy: "недостоверные показания", unknown: "состояние неизвестно",
};

function criticalAttr(node, name, value) {
  if (typeof node.setAttribute === "function") node.setAttribute(name, value);
}

export function applyCriticalNotifications(panel) {
  const response = panel._criticalNotifications;
  if (response && Array.isArray(response.notifications)) {
    panel._criticalNotificationsActive = response.notifications.filter((item) => item && typeof item.message === "string" && item.message);
  }
  const items = panel._criticalNotificationsActive || [];
  const signature = items.map((item) => `${item.role}:${item.reason}:${item.message}`).join("|");
  // Новая пара «список неисправностей» снимает ручное скрытие: владелец
  // должен увидеть обновившийся набор отказов.
  if (panel._criticalSignature !== signature) {
    panel._criticalSignature = signature;
    panel._criticalDismissed = false;
  }
  let node = panel._criticalNotificationsNode;
  if (!node) {
    node = document.createElement("section");
    node.className = "critical-notices";
    criticalAttr(node, "role", "alert");
    criticalAttr(node, "aria-live", "assertive");
    criticalAttr(node, "aria-atomic", "true");
    panel._criticalNotificationsNode = node;
    const container = panel._shell && panel._shell.container;
    if (container) container.appendChild(node);
  }
  node.hidden = !items.length || panel._criticalDismissed === true;
  node.textContent = "";
  if (!items.length || panel._criticalDismissed === true) return;
  const expanded = panel._criticalExpanded === true;
  const header = document.createElement("div");
  header.className = "critical-notices-header";
  const title = document.createElement("strong");
  title.className = "critical-notices-title";
  title.textContent = `Критические отказы датчиков: ${items.length}`;
  header.appendChild(title);
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "critical-notices-toggle";
  criticalAttr(toggle, "aria-expanded", expanded ? "true" : "false");
  toggle.textContent = expanded ? "Скрыть список" : "Показать";
  toggle.addEventListener("click", () => {
    panel._criticalExpanded = !panel._criticalExpanded;
    applyCriticalNotifications(panel);
  });
  header.appendChild(toggle);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "critical-notices-close";
  criticalAttr(close, "aria-label", "Скрыть предупреждение до изменения списка отказов");
  close.textContent = "×";
  close.addEventListener("click", () => {
    panel._criticalDismissed = true;
    applyCriticalNotifications(panel);
  });
  header.appendChild(close);
  node.appendChild(header);
  if (!expanded) return;
  for (const item of items) {
    const row = document.createElement("p");
    row.className = "critical-notices-item";
    const message = document.createElement("strong");
    message.className = "critical-notices-message";
    message.textContent = item.message;
    const reason = document.createElement("span");
    reason.className = "critical-notices-reason";
    reason.textContent = `${CRITICAL_TEXT[item.role] || "Датчик"} · причина: ${CRITICAL_TEXT[item.reason] || "неизвестна"}`;
    row.appendChild(message);
    row.appendChild(reason);
    node.appendChild(row);
  }
}
