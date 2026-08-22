import { enhanceAppendedModal } from "./hausman-hub-modal.js?v=1.52.148";

/* Modal with the nearest scenario runs, opened from the greeting-row button. */
export function openUpcomingEventsModal(panel, container, deps, events, appendRow) {
  const { el, svgIcon, setAttr } = deps;
  const existing = container.querySelector && container.querySelector(".overview-canon-events-backdrop");
  if (existing && existing.remove) existing.remove();
  const backdrop = el("div", "overview-canon-events-backdrop");
  const sheet = el("section", "overview-canon-events-sheet");
  setAttr(sheet, "role", "dialog");
  setAttr(sheet, "aria-modal", "true");
  setAttr(sheet, "aria-label", "Ближайшие события");
  const head = el("div", "overview-canon-section-head");
  head.appendChild(el("h2", null, "Ближайшие события"));
  const close = el("button", "overview-canon-events-close");
  close.type = "button";
  close.appendChild(svgIcon("close"));
  setAttr(close, "aria-label", "Закрыть");
  head.appendChild(close);
  sheet.appendChild(head);
  const dismiss = () => { if (backdrop.remove) backdrop.remove(); };
  close.addEventListener("click", dismiss);
  backdrop.addEventListener("click", (click) => { if (click.target === backdrop) dismiss(); });
  if (!events.length) {
    sheet.appendChild(el("div", "card empty-state muted", "Нет запланированных событий"));
  } else {
    const list = el("div", "overview-canon-upcoming-list");
    events.forEach((event) => appendRow(panel, list, event, deps));
    sheet.appendChild(list);
  }
  backdrop.appendChild(sheet);
  container.appendChild(backdrop);
  enhanceAppendedModal(backdrop, sheet, dismiss);
}
