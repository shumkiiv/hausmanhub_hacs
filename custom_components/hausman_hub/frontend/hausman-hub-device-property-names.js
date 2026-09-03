import { withCorrelationId } from "./hausman-hub-correlation.js?v=1.52.209";

const API = "hausman_hub/v1/device-property-names";

async function rename(panel, state, entity, name, reload, repaint) {
  if (!panel._hass || state.action) return;
  state.action = "rename-property";
  state.message = "";
  state.messageTone = "";
  repaint();
  try {
    const receipt = await panel._hass.callApi("POST", API, withCorrelationId(API, {
      contract: { name: "hausman-hub-device-property-name-request", version: 1 },
      entityId: entity.id,
      name,
    }));
    state.message = receipt && receipt.result === "reset"
      ? "Исходное название свойства восстановлено в Home Assistant."
      : "Название свойства сохранено в Home Assistant.";
    state.messageTone = "is-success";
    state.data = null;
    await reload();
    if (typeof panel._load === "function") await panel._load();
  } catch (error) {
    state.message = error && error.status === 404
      ? "Свойство уже удалено или изменилось в Home Assistant."
      : "Переименование не выполнено. Home Assistant не изменён.";
    state.messageTone = "is-error";
  } finally {
    state.action = "";
    if (typeof panel._render === "function") panel._render();
    else repaint();
  }
}

export function propertyNamesSection({ panel, el, state, reload, repaint }, entities) {
  const names = el("section", "device-maintenance-property-names");
  names.appendChild(el("h4", null, "Названия свойств и событий"));
  names.appendChild(el("p", "muted", "Меняются подписи в Hausman и Home Assistant. Технические идентификаторы и сценарии сохраняются."));
  entities.forEach((item) => {
    const row = el("div", "device-maintenance-property-name-row");
    const field = el("label", null);
    field.appendChild(el("span", null, item.id || "Свойство"));
    const input = el("input");
    input.value = item.name || "";
    input.maxLength = 255;
    input.disabled = Boolean(state.action);
    input.setAttribute("aria-label", `Название свойства ${item.name || item.id || ""}`);
    field.appendChild(input);
    row.appendChild(field);
    const save = el("button", "secondary", state.action === "rename-property" ? "Сохраняем…" : "Сохранить");
    save.type = "button";
    save.disabled = Boolean(state.action) || !input.value.trim();
    save.addEventListener("click", () => rename(panel, state, item, input.value.trim(), reload, repaint));
    row.appendChild(save);
    const reset = el("button", "secondary", "Сбросить");
    reset.type = "button";
    reset.disabled = Boolean(state.action);
    reset.title = "Вернуть исходное название, которое передаёт интеграция устройства.";
    reset.addEventListener("click", () => rename(panel, state, item, null, reload, repaint));
    row.appendChild(reset);
    names.appendChild(row);
  });
  return names;
}
