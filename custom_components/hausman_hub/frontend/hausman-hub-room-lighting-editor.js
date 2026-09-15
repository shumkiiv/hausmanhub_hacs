/* Full-size room lighting settings screen opened from the "Освещение" tab.
 *
 * This module is only a frame: the back action, the room heading and the
 * `.rls-*` layout. All field editing, drafts, previews and conflict recovery
 * live in the shared `hausman-hub-room-lighting.js` editor, so the project
 * never grows a second editor with the `.rle-*` prefix. The `.rls-*` names are
 * unique to this screen and cannot collide with the editor styles.
 */

import { renderRoomLightingEditor } from "./hausman-hub-room-lighting.js?v=1.52.286";

export function openRoomLightingSettings(panel, roomId, roomName) {
  // Only a real room id opens the editor. A name fallback would be rejected
  // by the server on save, so it is never used.
  if (!roomId) return false;
  panel._lightingRoomEditor = { roomId, roomName: roomName || roomId };
  if (panel._sectionRenderKeys) panel._sectionRenderKeys.lighting = null;
  panel._render();
  return true;
}

export function closeRoomLightingSettings(panel) {
  panel._lightingRoomEditor = null;
  if (panel._sectionRenderKeys) panel._sectionRenderKeys.lighting = null;
  panel._render();
}

export function renderRoomLightingSettingsScreen(panel, container, deps) {
  const selection = panel._lightingRoomEditor;
  const { el } = deps;
  container.innerHTML = "";
  if (!selection || !selection.roomId) {
    panel._lightingRoomEditor = null;
    const empty = el("section", "rls-screen");
    empty.appendChild(el("p", "rls-error", "Комната не выбрана. Вернитесь к списку комнат."));
    container.appendChild(empty);
    return;
  }
  const screen = el("section", "rls-screen");
  if (typeof deps.setAttr === "function") {
    deps.setAttr(screen, "aria-label", `Настройки света · ${selection.roomName || selection.roomId}`);
    deps.setAttr(screen, "data-testid", "room-lighting-settings");
  }
  const header = el("header", "rls-header");
  const back = el("button", "rls-back", "← Назад");
  back.type = "button";
  if (typeof deps.setAttr === "function") {
    deps.setAttr(back, "aria-label", "Вернуться к комнатам");
    deps.setAttr(back, "data-testid", "room-lighting-back");
  }
  back.addEventListener("click", () => closeRoomLightingSettings(panel));
  header.appendChild(back);
  const copy = el("div", "rls-heading");
  copy.appendChild(el("span", "rls-eyebrow", "НАСТРОЙКА СВЕТА"));
  copy.appendChild(el("h2", null, selection.roomName || selection.roomId));
  copy.appendChild(el("p", "rls-hint", "Событие, условия, решение и свет. Предпросмотр не сохраняет данные и не отправляет команд устройству."));
  header.appendChild(copy);
  screen.appendChild(header);
  const body = el("div", "rls-body");
  screen.appendChild(body);
  container.appendChild(screen);
  renderRoomLightingEditor(panel, body, { id: selection.roomId, name: selection.roomName }, deps, { screen: true });
}
