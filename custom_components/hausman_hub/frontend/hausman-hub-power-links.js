const POWER_LINKS_API = "hausman_hub/v1/admin/device-power-dependencies";
const CONTROLLABLE_DOMAINS = new Set([
  "button", "climate", "cover", "fan", "humidifier", "light", "lock",
  "media_player", "number", "select", "siren", "switch", "valve",
]);
const POWER_SOURCE_DOMAINS = new Set(["light", "switch"]);

function cloneDependencies(value) {
  return Array.isArray(value) ? value.map((item) => ({ ...item })) : [];
}

function candidateLabel(candidate) {
  return [candidate.name, candidate.roomName, candidate.entityId]
    .filter(Boolean)
    .join(" · ");
}

export function powerLinkCandidates(snapshot) {
  const found = new Map();
  const add = (item, fallback) => {
    const entityId = String(item?.entityId || "");
    const domain = String(item?.domain || entityId.split(".", 1)[0] || "");
    if (!entityId || !CONTROLLABLE_DOMAINS.has(domain) || found.has(entityId)) return;
    found.set(entityId, {
      entityId,
      domain,
      name: String(item?.name || item?.label || fallback?.name || entityId),
      roomName: String(item?.roomName || fallback?.roomName || ""),
    });
  };
  (snapshot?.devices || []).forEach((device) => {
    add(device, device);
    (device.details || []).forEach((detail) => add(detail, device));
  });
  return [...found.values()].sort((left, right) => (
    `${left.roomName}\u0000${left.name}`.localeCompare(
      `${right.roomName}\u0000${right.name}`,
      "ru",
    )
  ));
}

export function validatePowerLinkDraft(dependencies) {
  const seen = new Set();
  const graph = new Map();
  for (const dependency of dependencies) {
    const dependent = String(dependency.dependentEntityId || "");
    const source = String(dependency.powerSourceEntityId || "");
    if (!dependent || !source) return "Выберите устройство и источник питания для каждой связи.";
    if (dependent === source) return "Устройство нельзя связать с самим собой.";
    if (seen.has(dependent)) return "Для одного устройства можно выбрать только один источник питания.";
    seen.add(dependent);
    graph.set(dependent, source);
    if (dependency.policy === "auto_turn_on") {
      const warmup = Number(dependency.warmupSeconds);
      if (!Number.isInteger(warmup) || warmup < 0 || warmup > 30) {
        return "Задержка включения должна быть целым числом от 0 до 30 секунд.";
      }
    }
  }
  const visited = new Set();
  const visiting = new Set();
  const visit = (entityId) => {
    if (visiting.has(entityId)) return false;
    if (visited.has(entityId)) return true;
    visiting.add(entityId);
    const source = graph.get(entityId);
    if (source && !visit(source)) return false;
    visiting.delete(entityId);
    visited.add(entityId);
    return true;
  };
  for (const entityId of graph.keys()) {
    if (!visit(entityId)) return "Связи образуют замкнутый круг. Выберите другой источник питания.";
  }
  return "";
}

function powerLinksDirty(state) {
  return JSON.stringify(state.draft) !== JSON.stringify(state.data?.dependencies || []);
}

export async function loadPowerLinks(panel, force = false) {
  const state = panel._powerLinks;
  if (!panel._hass || state.loading || (powerLinksDirty(state) && !force)) return;
  state.loading = true;
  state.error = "";
  panel._render();
  try {
    const document = await panel._hass.callApi("GET", POWER_LINKS_API);
    state.data = document;
    state.draft = cloneDependencies(document.dependencies);
    state.status = "";
  } catch (error) {
    state.error = "Не удалось загрузить связи питания.";
  } finally {
    state.loading = false;
    panel._render();
  }
}

async function savePowerLinks(panel) {
  const state = panel._powerLinks;
  const error = validatePowerLinkDraft(state.draft);
  if (error) {
    state.status = error;
    panel._render();
    return;
  }
  if (panel._busy || !state.data || !powerLinksDirty(state)) return;
  panel._busy = true;
  state.status = "Сохраняю связи питания...";
  panel._render();
  try {
    const document = await panel._hass.callApi("PUT", POWER_LINKS_API, {
      expectedRevision: state.data.revision,
      dependencies: state.draft,
    });
    state.data = document;
    state.draft = cloneDependencies(document.dependencies);
    state.status = "Связи сохранены. Команды устройствам не отправлялись.";
    panel._notice = state.status;
    await panel._load();
  } catch (error) {
    state.status = error?.status === 409
      ? "Связи уже изменились. Отмените локальные изменения, обновите список и повторите сохранение."
      : "Сохранить связи не удалось. Команды устройствам не отправлялись.";
  } finally {
    panel._busy = false;
    panel._render();
  }
}

function optionSelect(panel, candidates, value, placeholder, excludedEntityId, onChange) {
  const select = document.createElement("select");
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = placeholder;
  select.appendChild(empty);
  candidates.forEach((candidate) => {
    const option = document.createElement("option");
    option.value = candidate.entityId;
    option.textContent = candidateLabel(candidate);
    option.disabled = candidate.entityId === excludedEntityId;
    select.appendChild(option);
  });
  if (value && !candidates.some((candidate) => candidate.entityId === value)) {
    const missing = document.createElement("option");
    missing.value = value;
    missing.textContent = `${value} · нет в текущем снимке`;
    select.appendChild(missing);
  }
  select.value = value || "";
  select.disabled = panel._busy;
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

function renderLinkRow(panel, dependency, index, candidates, helpers) {
  const { el } = helpers;
  const state = panel._powerLinks;
  const row = el("article", "power-link-row");
  const dependentField = el("label", "settings-field");
  dependentField.appendChild(el("span", "assistant-field-label", "Управляемое устройство"));
  dependentField.appendChild(optionSelect(
    panel,
    candidates,
    dependency.dependentEntityId,
    "Выберите устройство",
    dependency.powerSourceEntityId,
    (value) => {
      dependency.dependentEntityId = value;
      state.status = "";
      panel._render();
    },
  ));
  row.appendChild(dependentField);

  const sourceField = el("label", "settings-field");
  sourceField.appendChild(el("span", "assistant-field-label", "Источник питания"));
  sourceField.appendChild(optionSelect(
    panel,
    candidates.filter((candidate) => POWER_SOURCE_DOMAINS.has(candidate.domain)),
    dependency.powerSourceEntityId,
    "Выберите выключатель или реле",
    dependency.dependentEntityId,
    (value) => {
      dependency.powerSourceEntityId = value;
      state.status = "";
      panel._render();
    },
  ));
  row.appendChild(sourceField);

  const policyField = el("label", "settings-field");
  policyField.appendChild(el("span", "assistant-field-label", "Поведение"));
  const policy = document.createElement("select");
  [
    ["auto_turn_on", "Включать питание автоматически"],
    ["requires_on", "Только блокировать без питания"],
  ].forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    policy.appendChild(option);
  });
  policy.value = dependency.policy || "auto_turn_on";
  policy.disabled = panel._busy;
  policy.addEventListener("change", () => {
    dependency.policy = policy.value;
    if (policy.value === "auto_turn_on" && !Number.isInteger(dependency.warmupSeconds)) {
      dependency.warmupSeconds = 1;
    }
    if (policy.value === "requires_on") delete dependency.warmupSeconds;
    state.status = "";
    panel._render();
  });
  policyField.appendChild(policy);
  row.appendChild(policyField);

  if (dependency.policy === "auto_turn_on") {
    const warmupField = el("label", "settings-field power-link-warmup");
    warmupField.appendChild(el("span", "assistant-field-label", "Задержка, секунд"));
    const warmup = document.createElement("input");
    warmup.type = "number";
    warmup.min = "0";
    warmup.max = "30";
    warmup.step = "1";
    warmup.value = String(dependency.warmupSeconds ?? 1);
    warmup.disabled = panel._busy;
    warmup.addEventListener("input", () => {
      dependency.warmupSeconds = Number(warmup.value);
      state.status = "";
    });
    warmupField.appendChild(warmup);
    row.appendChild(warmupField);
  }

  const remove = el("button", "secondary power-link-remove", "Удалить связь");
  remove.type = "button";
  remove.disabled = panel._busy;
  remove.addEventListener("click", () => {
    state.draft.splice(index, 1);
    state.status = "";
    panel._render();
  });
  row.appendChild(remove);
  return row;
}

export function renderPowerLinks(panel, container, helpers) {
  const { el } = helpers;
  const state = panel._powerLinks;
  const intro = el("section", "card settings-card power-links-intro");
  intro.appendChild(el("h3", null, "Связи питания устройств"));
  intro.appendChild(el(
    "p",
    "muted settings-card-intro",
    "Свяжите люстру или другое устройство с выключателем, реле либо питающей линией. Перед любой командой Hausman Hub сначала включит источник, дождётся подтверждения и только потом обратится к устройству.",
  ));
  intro.appendChild(el(
    "div",
    "power-links-safety-note",
    "После команды источник остаётся включённым. Люстру можно выключить логически, сохранив её питание и связь с Zigbee-сетью.",
  ));
  container.appendChild(intro);

  if (state.loading && !state.data) {
    container.appendChild(el("section", "card settings-card", "Загружаю связи питания..."));
    return;
  }
  if (state.error && !state.data) {
    const failed = el("section", "card settings-card");
    failed.appendChild(el("p", "settings-inline-error", state.error));
    const retry = el("button", "secondary", "Повторить");
    retry.addEventListener("click", () => loadPowerLinks(panel, true));
    failed.appendChild(retry);
    container.appendChild(failed);
    return;
  }
  if (!state.data) return;

  const snapshot = panel._homeDashboard || panel._data?.snapshot || {};
  const candidates = powerLinkCandidates(snapshot);
  const editor = el("section", "card settings-card power-links-editor");
  const heading = el("div", "power-links-heading");
  const copy = el("div");
  copy.appendChild(el("strong", null, `Настроено связей: ${state.draft.length}`));
  copy.appendChild(el("small", null, "Одна сущность может зависеть только от одного источника."));
  heading.appendChild(copy);
  const add = el("button", "secondary", "Добавить связь");
  add.type = "button";
  add.disabled = panel._busy || state.draft.length >= 128;
  add.addEventListener("click", () => {
    state.draft.push({
      dependentEntityId: "",
      powerSourceEntityId: "",
      policy: "auto_turn_on",
      warmupSeconds: 1,
    });
    state.status = "";
    panel._render();
  });
  heading.appendChild(add);
  editor.appendChild(heading);
  if (!state.draft.length) {
    editor.appendChild(el("p", "muted power-links-empty", "Связей пока нет. Добавьте первую, чтобы источник питания включался перед командой устройству."));
  }
  const list = el("div", "power-links-list");
  state.draft.forEach((dependency, index) => {
    list.appendChild(renderLinkRow(panel, dependency, index, candidates, helpers));
  });
  editor.appendChild(list);
  if (state.status) editor.appendChild(el("p", "power-links-status", state.status));
  const actions = el("div", "settings-page-actions");
  const cancel = el("button", "secondary", "Отменить изменения");
  cancel.type = "button";
  cancel.disabled = panel._busy || !powerLinksDirty(state);
  cancel.addEventListener("click", () => {
    state.draft = cloneDependencies(state.data.dependencies);
    state.status = "";
    panel._render();
  });
  const refresh = el("button", "secondary", "Обновить");
  refresh.type = "button";
  refresh.disabled = panel._busy || powerLinksDirty(state);
  refresh.addEventListener("click", () => loadPowerLinks(panel, true));
  const save = el("button", "primary", "Сохранить связи");
  save.type = "button";
  save.disabled = panel._busy || !powerLinksDirty(state);
  save.addEventListener("click", () => savePowerLinks(panel));
  actions.appendChild(cancel);
  actions.appendChild(refresh);
  actions.appendChild(save);
  editor.appendChild(actions);
  container.appendChild(editor);
}
