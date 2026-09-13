/* Full-size room lighting settings editor opened from the Lighting tab.
   The stored configuration stays the source of truth: this module keeps a
   local draft, never invents server rules and never sends a physical command
   from the preview. */

import { apiErrorMessage, resolveApiError } from "./hausman-hub-error-taxonomy.js?v=1.52.271";

const BASE = (roomId) => `hausman_hub/v1/rooms/${encodeURIComponent(roomId)}/lighting`;

export const ROOM_LIGHTING_SECTIONS = [
  ["overview", "Обзор"],
  ["lights", "Светильники и роли"],
  ["inputs", "Датчики и выключатели"],
  ["periods", "Периоды"],
  ["conditions", "Условия и гашение"],
  ["manual-away", "Ручное управление и уход"],
  ["review-save", "Проверка и сохранение"],
];

const TYPE_LABELS = { light: "Светильник", sensor: "Датчик", switch: "Выключатель" };

function ensureStyles() {
  const id = "rle-styles";
  if (document.getElementById(id)) return;
  const link = document.createElement("link");
  link.id = id;
  link.rel = "stylesheet";
  link.href = "./hausman-hub-room-lighting-editor.css?v=1.52.271";
  document.head.appendChild(link);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value ?? null));
}

function changedKeys(baseline, draft, keys) {
  return keys.filter((key) => JSON.stringify(baseline ? baseline[key] : null) !== JSON.stringify(draft ? draft[key] : null));
}

export async function renderRoomLightingEditor(panel, container, { roomId, onBack }) {
  const state = {
    roomId,
    loading: true,
    saving: false,
    previewing: false,
    error: "",
    notice: "",
    conflict: false,
    baseline: null,
    draft: null,
    catalog: [],
    templates: [],
    status: null,
    preview: null,
    section: "overview",
    advanced: new Set(),
    previewTimer: null,
    saveController: null,
  };

  ensureStyles();
  container.textContent = "";
  const root = el("section", "rle-screen");
  root.setAttribute("aria-label", "Настройки света комнаты");

  const header = el("header", "rle-header");
  const back = el("button", "rle-back", "Назад");
  back.type = "button";
  back.addEventListener("click", () => leaveEditor());
  header.appendChild(back);
  const title = el("div", "rle-title");
  title.appendChild(el("h2", null, "Настройки света"));
  title.appendChild(el("p", "rle-subtitle", roomId));
  header.appendChild(title);
  const live = el("p", "rle-live");
  live.setAttribute("aria-live", "polite");
  header.appendChild(live);
  root.appendChild(header);

  const body = el("div", "rle-body");
  const nav = el("nav", "rle-nav");
  nav.setAttribute("aria-label", "Разделы настроек света");
  const content = el("div", "rle-content");
  body.appendChild(nav);
  body.appendChild(content);
  root.appendChild(body);

  const saveBar = el("footer", "rle-save-bar");
  const saveState = el("span", "rle-save-state", "");
  const saveButton = el("button", "rle-save", "Сохранить");
  saveButton.type = "button";
  saveButton.addEventListener("click", () => saveDraft());
  const reload = el("button", "rle-reload", "Загрузить заново");
  reload.type = "button";
  reload.addEventListener("click", () => load());
  saveBar.appendChild(saveState);
  saveBar.appendChild(reload);
  saveBar.appendChild(saveButton);
  root.appendChild(saveBar);
  container.appendChild(root);

  function commit(stateField, value) {
    if (!state.draft) return;
    state.draft[stateField] = value;
    markDirty();
  }

  function markDirty() {
    schedulePreview();
    render();
  }

  function schedulePreview() {
    if (state.previewTimer) window.clearTimeout(state.previewTimer);
    state.previewTimer = window.setTimeout(() => void runPreview(), 600);
  }

  async function runPreview() {
    if (!state.draft || state.saving) return;
    state.previewing = true;
    try {
      state.preview = await panel._hass.callApi("POST", `${BASE(roomId)}/preview`, state.draft);
      state.error = "";
    } catch (error) {
      if (resolveApiError(error).code === "revision_conflict") {
        state.conflict = true;
      }
      state.preview = null;
    } finally {
      state.previewing = false;
      render();
    }
  }

  async function load() {
    state.loading = true;
    state.error = "";
    state.conflict = false;
    render();
    try {
      const [config, catalog, templates, status] = await Promise.all([
        panel._hass.callApi("GET", `${BASE(roomId)}/config`).catch((error) => (error && error.status === 404 ? null : Promise.reject(error))),
        panel._hass.callApi("GET", `${BASE(roomId)}/editor-catalog`).catch(() => ({ devices: [] })),
        panel._hass.callApi("GET", `${BASE(roomId)}/templates`).catch(() => ({ templates: [] })),
        panel._hass.callApi("GET", `${BASE(roomId)}/status`).catch(() => null),
      ]);
      state.catalog = catalog?.devices || [];
      state.templates = templates?.templates || [];
      state.status = status;
      if (config) {
        state.baseline = config;
        state.draft = clone(config);
      } else {
        const template = state.templates[0];
        state.baseline = null;
        state.draft = template
          ? { ...clone(template), roomId, version: 1, updatedAt: Math.floor(Date.now() / 1000) }
          : null;
      }
    } catch (error) {
      state.error = apiErrorMessage(error);
    } finally {
      state.loading = false;
      render();
    }
  }

  async function saveDraft() {
    if (!state.draft || state.saving) return;
    state.saving = true;
    state.error = "";
    state.notice = "";
    render();
    try {
      const saved = await panel._hass.callApi("PUT", `${BASE(roomId)}/config`, state.draft);
      state.baseline = saved;
      state.draft = clone(saved);
      state.notice = "Сохранено";
      state.conflict = false;
      state.preview = null;
    } catch (error) {
      const policy = resolveApiError(error);
      if (policy.code === "revision_conflict") {
        state.conflict = true;
        state.error = "Настройки изменились на другом клиенте. Локальные правки сохранены.";
      } else {
        state.error = apiErrorMessage(error);
      }
    } finally {
      state.saving = false;
      render();
    }
  }

  function leaveEditor() {
    if (!state.draft || JSON.stringify(state.draft) !== JSON.stringify(state.baseline)) {
      if (!window.confirm("Есть несохранённые изменения. Выйти без сохранения?")) return;
    }
    if (typeof onBack === "function") onBack();
  }

  function decisionText() {
    const source = state.status?.sources?.[0];
    if (!source) return "Событие -> Условия -> Решение -> Свет";
    const ownership = source.ownership === "manual" ? "светом управляет человек" : "автоматика имеет владение";
    return `Событие -> Условия -> Решение -> Свет. Текущее состояние: ${source.state}, ${ownership}.`;
  }

  function renderNav() {
    nav.textContent = "";
    for (const [key, label] of ROOM_LIGHTING_SECTIONS) {
      const button = el("button", `rle-nav-item${state.section === key ? " is-active" : ""}`, label);
      button.type = "button";
      button.addEventListener("click", () => { state.section = key; render(); });
      nav.appendChild(button);
    }
  }

  function renderSection() {
    content.textContent = "";
    content.appendChild(el("p", "rle-decision", decisionText()));
    if (state.conflict) {
      const conflict = el("div", "rle-conflict");
      conflict.appendChild(el("p", null, "Конфликт версии: свежие настройки загружены не будут, пока вы не решите."));
      content.appendChild(conflict);
    }
    if (state.preview?.sectionIssues?.length) {
      const issues = el("ul", "rle-issues");
      for (const issue of state.preview.sectionIssues) {
        issues.appendChild(el("li", null, `${issue.section}: ${issue.message}`));
      }
      content.appendChild(issues);
    }
    const heading = ROOM_LIGHTING_SECTIONS.find(([key]) => key === state.section);
    content.appendChild(el("h3", null, heading ? heading[1] : ""));
    content.appendChild(renderSectionBody());
  }

  function renderSectionBody() {
    const draft = state.draft;
    if (!draft) return el("p", "rle-empty", "Настройка не найдена. Выберите шаблон и сохраните.");
    switch (state.section) {
      case "overview":
        return renderOverview(draft);
      case "lights":
        return renderTargets(draft);
      case "inputs":
        return renderInputs(draft);
      case "periods":
        return renderPeriods(draft);
      case "conditions":
        return renderConditions(draft);
      case "manual-away":
        return renderManual(draft);
      default:
        return renderReview();
    }
  }

  function renderOverview(draft) {
    const list = el("div", "rle-list");
    list.appendChild(readonlyRow("Профиль", draft.name || "—"));
    list.appendChild(readonlyRow("Версия", String(draft.version ?? "—")));
    list.appendChild(readonlyRow("Автоподхват", draft.autoAdopt === false ? "Выключен" : "Включён"));
    list.appendChild(explain("Схема решения показывает, почему автоматика включает или не включает свет. Она обновляется по безопасному предпросмотру."));
    return list;
  }

  function renderTargets(draft) {
    const list = el("div", "rle-list");
    for (const target of draft.devices?.light_targets || []) {
      const row = el("label", "rle-row");
      row.appendChild(el("span", null, target.name || target.id));
      const auto = el("input");
      auto.type = "checkbox";
      auto.checked = target.autoControl !== false;
      auto.addEventListener("change", () => { target.autoControl = auto.checked; markDirty(); });
      row.appendChild(auto);
      row.appendChild(el("small", null, "Авто"));
      list.appendChild(row);
    }
    const devices = state.catalog.length ? state.catalog : [];
    if (devices.length) {
      list.appendChild(el("h4", null, "Найденные устройства комнаты"));
      for (const device of devices) {
        list.appendChild(el("p", "rle-device",
          `${device.label} · ${TYPE_LABELS[device.kind] || device.kind}${device.available ? "" : " · нет связи"}`));
      }
    }
    return list;
  }

  function renderInputs(draft) {
    const list = el("div", "rle-list");
    for (const sensor of draft.devices?.sensors || []) {
      list.appendChild(readonlyRow(sensor.name || sensor.id, sensor.entityId || "—"));
    }
    return list;
  }

  function renderPeriods(draft) {
    const list = el("div", "rle-list");
    for (const entry of draft.schedule || []) {
      list.appendChild(readonlyRow(entry.title || entry.id,
        `${entry.when?.anchor?.kind || "—"} · ${entry.how?.mode || "—"}`));
    }
    return list;
  }

  function renderConditions(draft) {
    const list = el("div", "rle-list");
    list.appendChild(readonlyRow("Плавное гашение", draft.dimming?.enabled ? `${draft.dimming.targetPercent}% за ${draft.dimming.fadeSeconds} с` : "Выключено"));
    const absence = draft.timers?.absence_seconds;
    list.appendChild(readonlyRow("Порог отсутствия", absence === undefined ? "—" : `${absence} с`));
    return list;
  }

  function renderManual(draft) {
    const list = el("div", "rle-list");
    const protection = draft.manualOffProtection || {};
    list.appendChild(readonlyRow("Защита после ручного выключения",
      protection.enabled ? `${protection.minimumIntervalSeconds} с · ${protection.releaseMode}` : "Выключена"));
    list.appendChild(readonlyRow("Уход/возврат", draft.awayBehavior?.mode || "—"));
    return list;
  }

  function renderReview() {
    const list = el("div", "rle-list");
    list.appendChild(el("p", "rle-device",
      state.preview?.safe === false
        ? "Предпросмотр нашёл ошибки: исправьте разделы выше."
        : "Предпросмотр безопасен: команды устройству не отправляются."));
    list.appendChild(explain("Реальная проверка доступна только после сохранения и явного подтверждения."));
    return list;
  }

  function readonlyRow(label, value) {
    const row = el("div", "rle-row");
    row.appendChild(el("span", null, label));
    row.appendChild(el("strong", null, value));
    return row;
  }

  function explain(text) {
    return el("p", "rle-hint", text);
  }

  function render() {
    live.textContent = state.error || state.notice || (state.previewing ? "Проверяем черновик…" : "");
    renderNav();
    if (state.loading) {
      content.textContent = "";
      content.appendChild(el("p", "rle-empty", "Загружаем настройки…"));
    } else {
      renderSection();
    }
    const dirty = state.draft ? changedKeys(state.baseline, state.draft, Object.keys(state.draft)).length : 0;
    saveState.textContent = dirty ? `Не сохранено: ${dirty} разд.` : "Сохранено";
    saveButton.disabled = state.saving || !state.draft;
    reload.hidden = !state.conflict;
  }

  await load();
  return root;
}
