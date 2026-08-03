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
