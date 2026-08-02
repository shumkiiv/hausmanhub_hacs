/* Shared, non-layout-shifting feedback policy for every panel operation. */

export function feedbackTone(message) {
  const text = String(message || "").trim().toLocaleLowerCase("ru");
  if (!text) return "info";
  if (/(не выполн|не удалось|не пройден|не ответил|не найден|недоступ|неизвест|ошиб|отклонен|отклонён|не сохран)/u.test(text)) return "error";
  if (/(частично|еще провер|ещё провер|подтверждение|ожида|введите|проверьте|изменил|не опубликован|повторите)/u.test(text)) return "warning";
  return "success";
}

export function applyFeedback(element, message, setAttr) {
  element.textContent = message || "";
  element.className = "notice";
  if (!message) {
    element.style.display = "none";
    return "info";
  }
  const tone = feedbackTone(message);
  element.className = `notice is-${tone}`;
  setAttr(element, "role", tone === "error" ? "alert" : "status");
  setAttr(element, "aria-live", tone === "error" ? "assertive" : "polite");
  element.style.display = "";
  return tone;
}
