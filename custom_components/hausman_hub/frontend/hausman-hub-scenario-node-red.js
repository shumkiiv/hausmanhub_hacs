/* Safe embedded editor for one Hausman-managed Node-RED function node. */

import { trapModalTabKey } from "./hausman-hub-modal.js?v=1.52.216";

function inputDeviceTitle(device, targetId) {
  return String(device && (device.physical_name || device.name) || targetId || "Источник недоступен").trim();
}

function inputDeviceDetails(device) {
  if (!device) return "Источник больше не найден в каталоге";
  const title = inputDeviceTitle(device, device.target_id).toLocaleLowerCase("ru");
  return [device.room_name, device.capability_name]
    .map((value) => String(value || "").trim())
    .filter((value, index, values) => value && !title.includes(value.toLocaleLowerCase("ru")) && values.indexOf(value) === index)
    .join(" · ");
}

function inputOptionTitle(device) {
  const title = inputDeviceTitle(device, device && device.target_id);
  const details = inputDeviceDetails(device);
  return details ? `${title} · ${details}` : title;
}

export function renderNodeRedInputPicker(metadata, devices, deps) {
  const { el, setAttr } = deps;
  const field = el("div", "scenario-field scenario-node-red-inputs");
  const label = el("label", null, "Добавить данные для алгоритма");
  setAttr(label, "for", "scenario-node-red-input-picker");
  field.appendChild(label);

  const candidates = Array.isArray(devices) ? devices : [];
  const byId = new Map(candidates.map((device) => [device.target_id, device]));
  const selected = el("section", "scenario-node-red-selected");
  setAttr(selected, "aria-label", "Выбранные данные алгоритма");
  setAttr(selected, "aria-live", "polite");
  field.appendChild(selected);

  const select = el("select");
  select.id = "scenario-node-red-input-picker";
  select.multiple = true;
  setAttr(select, "aria-label", "Добавить или убрать данные алгоритма");
  candidates.forEach((device) => {
    const option = el("option", null, inputOptionTitle(device));
    option.value = device.target_id;
    option.selected = (metadata.inputTargetIds || []).includes(device.target_id);
    select.appendChild(option);
  });

  const updateSelected = (targetIds) => {
    metadata.inputTargetIds = Array.from(new Set(targetIds.filter(Boolean))).slice(0, 32);
    Array.from(select.options).forEach((option) => {
      option.selected = metadata.inputTargetIds.includes(option.value);
    });
    renderSelected();
  };
  const renderSelected = () => {
    selected.innerHTML = "";
    const header = el("header");
    header.appendChild(el("strong", null, `Выбрано: ${(metadata.inputTargetIds || []).length}`));
    if ((metadata.inputTargetIds || []).length) {
      const clear = el("button", "scenario-node-red-selected-clear", "Очистить");
      clear.type = "button";
      setAttr(clear, "aria-label", "Очистить выбранные данные");
      clear.addEventListener("click", () => updateSelected([]));
      header.appendChild(clear);
    }
    selected.appendChild(header);
    if (!(metadata.inputTargetIds || []).length) {
      selected.appendChild(el("p", "scenario-node-red-selected-empty", "Ничего не выбрано"));
      return;
    }
    const list = el("div", "scenario-node-red-selected-list");
    (metadata.inputTargetIds || []).forEach((targetId) => {
      const device = byId.get(targetId);
      const title = inputDeviceTitle(device, targetId);
      const item = el("div", "scenario-node-red-selected-item");
      const text = el("span");
      text.appendChild(el("b", null, title));
      const details = inputDeviceDetails(device);
      if (details) text.appendChild(el("small", null, details));
      item.appendChild(text);
      const remove = el("button", "scenario-node-red-selected-remove", "×");
      remove.type = "button";
      setAttr(remove, "aria-label", `Убрать ${title}`);
      remove.addEventListener("click", () => updateSelected(metadata.inputTargetIds.filter((id) => id !== targetId)));
      item.appendChild(remove);
      list.appendChild(item);
    });
    selected.appendChild(list);
  };

  select.addEventListener("change", () => {
    const unresolved = (metadata.inputTargetIds || []).filter((targetId) => !byId.has(targetId));
    const visible = Array.from(select.selectedOptions).map((option) => option.value);
    updateSelected([...unresolved, ...visible]);
  });
  renderSelected();
  field.appendChild(select);
  field.appendChild(el("small", null, "Выберите датчики и свойства, нужные для ветвлений. Уже выбранные значения показаны отдельным списком выше."));
  field.appendChild(el("small", null, "Устройства из триггеров и действий добавятся автоматически."));
  return field;
}

export function renderDynamicNodeRedActions(scenario, deps) {
  const { el, setAttr } = deps;
  const section = el("section", "scenario-editor-panel");
  setAttr(section, "data-scenario-step", "action");
  section.appendChild(el("h3", null, "Выполнить"));
  const summary = el("div", "scenario-rule-list-summary");
  summary.appendChild(el("span", null, "Действия выбираются по текущим данным и ветке алгоритма"));
  summary.appendChild(el("b", "scenario-editor-badge", "Node-RED"));
  section.appendChild(summary);
  const plan = el("div", "scenario-node-red-plan");
  plan.appendChild(el("h4", null, "Динамический план действий"));
  plan.appendChild(el("p", null, scenario.actionDescription || "Состав команд определяет управляемая function Node-RED."));
  plan.appendChild(el("small", null, "Техническая пауза в хранилище не является действием дома. Точные команды и выбранную ветку покажет пробный запуск без их отправки."));
  section.appendChild(plan);
  return section;
}

function sourcePath(deps, scenarioId) {
  return `${deps.nodeRedApi}/source/${encodeURIComponent(scenarioId)}`;
}

export function managedSourceEditorDirty(panel) {
  const editor = panel._scenarioNodeRedEditor;
  return Boolean(editor && editor.document && editor.source !== editor.document.source);
}

export async function openManagedSourceEditor(panel, scenario, deps, refresh) {
  if (panel._scenarioNodeRedEditor || panel._busy) return;
  panel._scenarioNodeRedEditor = {
    scenarioId: scenario.id,
    title: scenario.title,
    loading: true,
    saving: false,
    validating: false,
    document: null,
    source: "",
    receipt: null,
    error: "",
  };
  refresh(panel);
  try {
    const document = await panel._hass.callApi("GET", sourcePath(deps, scenario.id));
    panel._scenarioNodeRedEditor.document = document;
    panel._scenarioNodeRedEditor.source = document.source;
  } catch (error) {
    const body = error && typeof error.body === "object" ? error.body : {};
    panel._scenarioNodeRedEditor.error = body.message || "Не удалось прочитать алгоритм из Node-RED.";
  } finally {
    panel._scenarioNodeRedEditor.loading = false;
    refresh(panel);
  }
}

export function closeManagedSourceEditor(panel, refresh, force = false) {
  if (!force && managedSourceEditorDirty(panel) && !window.confirm("Закрыть редактор алгоритма без сохранения?")) return;
  panel._scenarioNodeRedEditor = null;
  refresh(panel);
}

async function submitManagedSource(panel, deps, validateOnly, refresh) {
  const editor = panel._scenarioNodeRedEditor;
  if (!editor || !editor.document || editor.saving || editor.validating) return;
  editor.error = "";
  editor.receipt = null;
  editor.validating = validateOnly;
  editor.saving = !validateOnly;
  refresh(panel);
  try {
    const receipt = await panel._hass.callApi("PUT", sourcePath(deps, editor.scenarioId), {
      contract: { name: "hausman-hub-scenario-node-red-source-update-request", version: 1 },
      scenarioId: editor.scenarioId,
      expectedScenarioRevision: editor.document.scenarioRevision,
      expectedSourceHash: editor.document.sourceHash,
      source: editor.source,
      validateOnly,
    });
    editor.receipt = receipt;
    if (!validateOnly) {
      editor.document = {
        ...editor.document,
        scenarioRevision: receipt.scenarioRevision,
        flowRevision: receipt.flowRevision,
        sourceHash: receipt.currentSourceHash,
        syncStatus: "synced",
        generatedBy: "user",
        source: editor.source,
      };
      const scenario = panel._scenarioEditor;
      if (scenario && scenario.id === editor.scenarioId) {
        scenario.revision = receipt.scenarioRevision;
        scenario.definition.nodeRed = {
          ...(scenario.definition.nodeRed || {}),
          flowRevision: receipt.flowRevision,
          sourceHash: receipt.currentSourceHash,
          syncStatus: "synced",
          generatedBy: "user",
        };
      }
      panel._notice = receipt.saved ? "Алгоритм проверен и сохранён." : "Алгоритм уже был актуален.";
      panel._error = false;
    }
  } catch (error) {
    const body = error && typeof error.body === "object" ? error.body : {};
    const first = Array.isArray(body.violations) ? body.violations[0] : null;
    editor.error = first && first.message
      ? first.message
      : body.error === "source_conflict" || body.error === "revision_conflict"
        ? "Алгоритм изменён на другом устройстве. Закройте редактор и откройте его заново."
        : body.message || "Алгоритм не прошёл проверку.";
  } finally {
    editor.validating = false;
    editor.saving = false;
    refresh(panel);
  }
}

function renderTrace(editor, deps) {
  const verification = editor.receipt && editor.receipt.verification;
  if (!verification) return null;
  const { el } = deps;
  const section = el("section", "scenario-node-red-source-trace");
  section.appendChild(el("strong", null, verification.summary || "Пробный запуск завершён."));
  section.appendChild(el("small", null, `Ветка: ${verification.selectedBranch || "не выбрана"} · ${verification.durationMs || 0} мс · команд отправлено: нет`));
  const trace = Array.isArray(verification.trace) ? verification.trace : [];
  if (trace.length) {
    const list = el("ol");
    trace.forEach((item) => {
      const row = el("li", `is-${item.status || "skipped"}`);
      row.appendChild(el("b", null, item.title || item.id || "Проверка"));
      if (item.reason) row.appendChild(el("small", null, item.reason));
      list.appendChild(row);
    });
    section.appendChild(list);
  }
  return section;
}

export function renderManagedSourceEditor(panel, container, deps, refresh) {
  const editor = panel._scenarioNodeRedEditor;
  if (!editor) return;
  const { el, setAttr } = deps;
  const overlay = el("div", "scenario-node-red-source-overlay");
  const dialog = el("section", "scenario-node-red-source-dialog");
  setAttr(dialog, "role", "dialog");
  setAttr(dialog, "aria-modal", "true");
  setAttr(dialog, "aria-labelledby", "scenario-node-red-source-title");
  const header = el("header");
  const heading = el("div");
  const title = el("h2", null, "Алгоритм Node-RED");
  setAttr(title, "id", "scenario-node-red-source-title");
  heading.appendChild(title);
  heading.appendChild(el("p", null, editor.title));
  header.appendChild(heading);
  const close = el("button", "secondary", "×");
  close.type = "button";
  setAttr(close, "aria-label", "Закрыть редактор алгоритма");
  close.addEventListener("click", () => closeManagedSourceEditor(panel, refresh));
  header.appendChild(close);
  dialog.appendChild(header);

  if (editor.loading) {
    dialog.appendChild(el("p", "scenario-node-red-source-loading", "Читаю function из Node-RED…"));
  } else if (!editor.document) {
    dialog.appendChild(el("p", "scenario-node-red-source-error", editor.error || "Исходник недоступен."));
  } else {
    const safety = el("div", "scenario-node-red-source-safety");
    safety.appendChild(el("b", null, "Безопасное выполнение"));
    safety.appendChild(el("p", null, "Код выбирает ветку и возвращает план. Доступ к сети, секретам и прямая отправка команд запрещены. Перед сохранением Hausman делает пробный запуск и при ошибке возвращает прежнюю версию."));
    dialog.appendChild(safety);
    const meta = el("div", "scenario-node-red-source-meta");
    meta.appendChild(el("span", null, `Сценарий r${editor.document.scenarioRevision}`));
    meta.appendChild(el("span", null, `Flow r${editor.document.flowRevision}`));
    meta.appendChild(el("span", "scenario-node-red-source-size", `${new Blob([editor.source]).size} / ${editor.document.maxSourceBytes} байт`));
    meta.appendChild(el("span", `scenario-node-red-source-dirty ${managedSourceEditorDirty(panel) ? "is-dirty" : "is-synced"}`, managedSourceEditorDirty(panel) ? "Есть изменения" : "Синхронизирован"));
    dialog.appendChild(meta);
    const source = el("textarea", "scenario-node-red-source-code");
    source.value = editor.source;
    source.spellcheck = false;
    source.wrap = "off";
    setAttr(source, "aria-label", "Исходник function Node-RED");
    source.addEventListener("input", () => {
      editor.source = source.value;
      editor.receipt = null;
      editor.error = "";
      const dirty = managedSourceEditorDirty(panel);
      const dirtyLabel = dialog.querySelector(".scenario-node-red-source-dirty");
      const sizeLabel = dialog.querySelector(".scenario-node-red-source-size");
      const saveButton = dialog.querySelector(".scenario-node-red-source-save");
      if (dirtyLabel) {
        dirtyLabel.className = `scenario-node-red-source-dirty ${dirty ? "is-dirty" : "is-synced"}`;
        dirtyLabel.textContent = dirty ? "Есть изменения" : "Синхронизирован";
      }
      if (sizeLabel) sizeLabel.textContent = `${new Blob([editor.source]).size} / ${editor.document.maxSourceBytes} байт`;
      if (saveButton) saveButton.disabled = !dirty;
    });
    dialog.appendChild(source);
    if (editor.error) dialog.appendChild(el("p", "scenario-node-red-source-error", editor.error));
    if (editor.receipt && Array.isArray(editor.receipt.diagnostics)) {
      const diagnostics = el("ul", "scenario-node-red-source-diagnostics");
      editor.receipt.diagnostics.forEach((item) => diagnostics.appendChild(el("li", null, `${item.line ? `Строка ${item.line}: ` : ""}${item.message}`)));
      dialog.appendChild(diagnostics);
    }
    const trace = renderTrace(editor, deps);
    if (trace) dialog.appendChild(trace);
    const footer = el("footer");
    footer.appendChild(el("p", null, "Проверка не отправляет команды устройствам. Сохраняется только эта function."));
    const actions = el("div");
    const validate = el("button", "secondary", editor.validating ? "Проверяю…" : "Проверить");
    validate.type = "button";
    validate.disabled = editor.validating || editor.saving;
    validate.addEventListener("click", () => submitManagedSource(panel, deps, true, refresh));
    const save = el("button", "scenario-node-red-source-save", editor.saving ? "Сохраняю…" : "Проверить и сохранить");
    save.type = "button";
    save.disabled = editor.validating || editor.saving || !managedSourceEditorDirty(panel);
    save.addEventListener("click", () => submitManagedSource(panel, deps, false, refresh));
    actions.appendChild(validate);
    actions.appendChild(save);
    footer.appendChild(actions);
    dialog.appendChild(footer);
  }
  overlay.appendChild(dialog);
  overlay.addEventListener("click", (event) => { if (event.target === overlay) closeManagedSourceEditor(panel, refresh); });
  overlay.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Escape") {
      event.preventDefault();
      closeManagedSourceEditor(panel, refresh);
      return;
    }
    trapModalTabKey(event, dialog);
  });
  container.appendChild(overlay);
}
