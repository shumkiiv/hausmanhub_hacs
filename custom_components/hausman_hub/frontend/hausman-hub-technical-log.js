const LEVELS = new Set(["success", "warning", "error"]);
const EMPTY_ENTRY = { at: "—", level: "info", message: "Событий текущего сеанса пока нет" };

export function recordTechnicalEvent(entries, level, message) {
  const safeLevel = LEVELS.has(level) ? level : "info";
  const safeMessage = String(message || "Техническое событие")
    .replace(/https?:\/\/\S+/gi, "[адрес скрыт]")
    .slice(0, 180);
  const last = entries[entries.length - 1];
  if (last && last.level === safeLevel && last.message === safeMessage) return;
  entries.push({
    level: safeLevel,
    message: safeMessage,
    at: new Date().toLocaleTimeString("ru-RU", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }),
  });
  if (entries.length > 80) entries.splice(0, entries.length - 80);
}

export function technicalLogText(entries, version) {
  const rows = entries.length ? entries : [EMPTY_ENTRY];
  return [
    `HausmanHub ${version || "—"}`,
    "Технический журнал текущего сеанса",
    ...rows.map((entry) => `${entry.at} [${entry.level.toUpperCase()}] ${entry.message}`),
  ].join("\n");
}

export async function copyTechnicalLog(host) {
  try {
    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
      throw new Error("clipboard unavailable");
    }
    await navigator.clipboard.writeText(technicalLogText(
      host._technicalLog,
      host._data && host._data.integration_version,
    ));
    host._notice = "Технический журнал скопирован без адресов и данных устройств.";
  } catch (error) {
    host._notice = "Не удалось скопировать журнал. Разрешите доступ к буферу обмена.";
  }
  host._render();
}

export function renderTechnicalLogCard(host, container, { el }) {
  const card = el("section", "card settings-card technical-log-card");
  const head = el("div", "technical-log-head");
  const copy = el("div");
  copy.appendChild(el("h3", null, "Технический журнал"));
  copy.appendChild(el(
    "p", "muted settings-card-intro",
    "События и ошибки только текущего сеанса панели. Адреса и имена устройств сюда не записываются.",
  ));
  head.appendChild(copy);
  const copyButton = el("button", "secondary", "Копировать журнал");
  copyButton.type = "button";
  copyButton.addEventListener("click", () => copyTechnicalLog(host));
  head.appendChild(copyButton);
  card.appendChild(head);
  const list = el("div", "technical-log-list");
  const entries = host._technicalLog.length
    ? [...host._technicalLog].reverse().slice(0, 20)
    : [EMPTY_ENTRY];
  entries.forEach((entry) => {
    const row = el("div", `technical-log-row is-${entry.level}`);
    row.appendChild(el("time", null, entry.at));
    row.appendChild(el("span", "technical-log-dot"));
    row.appendChild(el("strong", null, entry.message));
    list.appendChild(row);
  });
  card.appendChild(list);
  container.appendChild(card);
}
