export const TABLET_PROFILE_API = "hausman_hub/v1/tablet-profile";

function intercomSettings(panel) {
  return panel._tabletProfile?.settings?.intercom || {};
}

function deviceId(device) {
  return String(device?.id || device?.deviceId || device?.physicalId || device?.entityId || "");
}

function isIntercomCatalogTarget(target) {
  const identity = [target?.name, target?.entity_id, target?.target_id]
    .filter(Boolean).join(" ").toLocaleLowerCase("ru");
  return /(домофон|intercom|doorbell|глазок)/.test(identity);
}

function isIntercomPhysicalDevice(device) {
  const details = Array.isArray(device?.details) ? device.details : [];
  const identity = [
    device?.name, device?.entityId, device?.physicalId, device?.model,
    ...details.flatMap((detail) => [detail?.label, detail?.entityId]),
  ].filter(Boolean).join(" ").toLocaleLowerCase("ru");
  return /(домофон|intercom|doorbell|глазок)/.test(identity);
}

export function applyTabletProfile(panel, profile) {
  if (!profile?.settings?.intercom || panel._intercomDirty) return;
  panel._tabletProfile = profile;
  panel._intercomDraft = { ...profile.settings.intercom };
}

export function isIntercomQuickAccessVisible(panel) {
  const settings = intercomSettings(panel);
  if (settings.showQuickAccess !== true || !settings.deviceId) return false;
  const catalog = Array.isArray(panel._scenarios?.catalog?.devices)
    ? panel._scenarios.catalog.devices : [];
  return Boolean(panel._resolveIntercomAction?.(settings.deviceId, catalog));
}

export function syncIntercomQuickAccess(panel) {
  const visible = isIntercomQuickAccessVisible(panel);
  [".header-intercom", ".sidebar-intercom", ".kiosk-intercom"].forEach((selector) => {
    const button = panel.shadowRoot?.querySelector?.(selector);
    if (button?.style) button.style.display = visible ? "" : "none";
  });
  return visible;
}

function candidates(panel) {
  const catalog = Array.isArray(panel._scenarios?.catalog?.devices)
    ? panel._scenarios.catalog.devices : [];
  const physical = panel._homeDevices("devices").filter(isIntercomPhysicalDevice).map((device) => {
    const id = String(device.entityId || deviceId(device));
    return {
      device,
      id,
      aliases: [device.id, device.deviceId, device.physicalId, device.entityId]
        .filter(Boolean).map((value) => String(value)),
      command: panel._resolveIntercomAction?.(id, catalog),
    };
  }).filter((item) => item.id && item.command?.targetId && item.command?.actionId);
  const usedTargets = new Set(physical.map((item) => item.command.targetId));
  const catalogOnly = catalog.filter((target) => (
    isIntercomCatalogTarget(target) && !usedTargets.has(target.target_id)
  )).map((target) => {
    const id = String(target.entity_id || target.target_id || "");
    const command = panel._resolveIntercomAction?.(id, catalog);
    return {
      id,
      aliases: [target.entity_id, target.target_id].filter(Boolean).map((value) => String(value)),
      command,
      device: {
        id,
        entityId: target.entity_id || null,
        name: target.name || target.entity_id || "Домофон",
        roomName: target.room_name || null,
        catalogOnly: true,
      },
    };
  }).filter((item) => item.id && item.command?.targetId && item.command?.actionId);
  return physical.concat(catalogOnly).sort((left, right) => (
    String(left.device.name || "").localeCompare(String(right.device.name || ""), "ru")
  ));
}

export async function saveIntercomSettings(panel) {
  if (panel._busy || !panel._tabletProfile || !panel._intercomDirty) return;
  panel._busy = true;
  panel._notice = "";
  panel._render();
  try {
    const settings = JSON.parse(JSON.stringify(panel._tabletProfile.settings));
    settings.intercom = { ...panel._intercomDraft };
    const saved = await panel._hass.callApi("PUT", TABLET_PROFILE_API, {
      expectedRevision: panel._tabletProfile.revision,
      settings,
    });
    panel._tabletProfile = saved;
    panel._intercomDraft = { ...saved.settings.intercom };
    panel._intercomDirty = false;
    panel._notice = saved.settings.intercom.deviceId
      ? "Домофон настроен. Быстрый доступ обновлён."
      : "Настройка домофона отключена. Быстрый доступ скрыт.";
  } catch (error) {
    panel._notice = error?.status === 409
      ? "Настройки планшета изменились в другом окне. Обновите страницу и повторите."
      : "Не удалось сохранить домофон. Выбранное устройство не изменено.";
  } finally {
    panel._busy = false;
    panel._render();
  }
}

export function renderIntercomSettings(panel, container, deps) {
  const { el, setAttr, svgIcon } = deps;
  const card = el("section", "card settings-card intercom-settings-card");
  const head = el("div", "intercom-settings-head");
  const icon = el("span", "intercom-settings-icon");
  icon.appendChild(svgIcon("intercom"));
  head.appendChild(icon);
  const copy = el("div");
  copy.appendChild(el("h3", null, "Домофон"));
  copy.appendChild(el("p", "muted", "Выберите одно устройство Home Assistant с опубликованной командой открытия. До сохранения кнопка домофона нигде не показывается."));
  head.appendChild(copy);
  card.appendChild(head);
  if (!panel._tabletProfile) {
    card.appendChild(el("div", "settings-empty-state", "Профиль планшета недоступен. Обновите состояние Home Assistant."));
    container.appendChild(card);
    return;
  }
  const available = candidates(panel);
  const currentId = String(panel._intercomDraft?.deviceId || "");
  const matchesCurrent = (item) => item.id === currentId
    || (Array.isArray(item.aliases) && item.aliases.includes(currentId));
  const current = currentId ? available.find(matchesCurrent) : null;
  const field = el("label", "settings-field");
  field.appendChild(el("span", "assistant-field-label", "Устройство домофона"));
  const select = el("select", "intercom-device-select");
  const empty = el("option", null, "Не настроен — скрыть быстрый доступ");
  empty.value = "";
  select.appendChild(empty);
  available.forEach((item) => {
    const option = el("option", null, `${item.device.name || "Домофон"}${item.device.roomName ? ` · ${item.device.roomName}` : ""}`);
    option.value = item.id;
    select.appendChild(option);
  });
  if (currentId && !current) {
    const missing = el("option", null, "Настроенное устройство больше недоступно");
    missing.value = currentId;
    select.appendChild(missing);
  }
  select.value = current ? current.id : currentId;
  field.appendChild(select);
  field.appendChild(el("small", "settings-field-help", available.length
    ? "Показаны только устройства, для которых Home Assistant предоставляет безопасную команду открытия без дополнительного значения."
    : "Подходящие устройства пока не найдены. Проверьте сущность и доступные действия в Home Assistant."));
  card.appendChild(field);
  const quickRow = el("div", "settings-toggle-row");
  const quickCopy = el("span", "settings-toggle-copy");
  quickCopy.appendChild(el("strong", null, "Показывать быстрый доступ"));
  quickCopy.appendChild(el("small", null, "Кнопка появится в шапке, боковом меню и киоске только после выбора рабочего устройства."));
  quickRow.appendChild(quickCopy);
  const quick = el("button", `settings-switch${panel._intercomDraft.showQuickAccess ? " is-on" : ""}`);
  quick.type = "button";
  quick.disabled = !currentId;
  setAttr(quick, "role", "switch");
  setAttr(quick, "aria-checked", panel._intercomDraft.showQuickAccess ? "true" : "false");
  setAttr(quick, "aria-label", "Показывать быстрый доступ к домофону");
  quick.appendChild(el("span", "settings-switch-status", panel._intercomDraft.showQuickAccess ? "Включено" : "Выключено"));
  const track = el("span", "settings-switch-track");
  track.appendChild(el("span", "settings-switch-knob"));
  quick.appendChild(track);
  quickRow.appendChild(quick);
  card.appendChild(quickRow);
  const status = el("div", `intercom-settings-status${current ? " is-ready" : " is-warning"}`);
  status.appendChild(el("strong", null, currentId ? "Проверка настройки" : "Домофон не настроен"));
  status.appendChild(el("span", null, current
    ? "Устройство найдено, команда открытия доступна."
    : "Быстрый доступ скрыт и команды не отправляются."));
  card.appendChild(status);
  const actions = el("div", "settings-page-actions intercom-settings-actions");
  const reset = el("button", "secondary", "Отменить изменения");
  reset.disabled = panel._busy || !panel._intercomDirty;
  const save = el("button", null, "Сохранить домофон");
  save.disabled = panel._busy || !panel._intercomDirty || (panel._intercomDraft.showQuickAccess && !panel._intercomDraft.deviceId);
  const changed = () => {
    panel._intercomDirty = JSON.stringify(panel._intercomDraft) !== JSON.stringify(intercomSettings(panel));
    panel._render();
  };
  select.addEventListener("change", () => {
    panel._intercomDraft.deviceId = select.value || null;
    if (!select.value) panel._intercomDraft.showQuickAccess = false;
    changed();
  });
  quick.addEventListener("click", () => {
    panel._intercomDraft.showQuickAccess = !panel._intercomDraft.showQuickAccess;
    changed();
  });
  reset.addEventListener("click", () => {
    panel._intercomDraft = { ...intercomSettings(panel) };
    panel._intercomDirty = false;
    panel._render();
  });
  save.addEventListener("click", () => saveIntercomSettings(panel));
  actions.appendChild(reset);
  actions.appendChild(save);
  card.appendChild(actions);
  container.appendChild(card);
}

export function renderAppearanceSettings(panel, container, deps) {
  const { el, selectField, setAttr, svgIcon, themeModeMeta } = deps;
  const card = el("section", "card settings-card interface-settings-card");
  card.appendChild(el("h3", null, "Вид и удобство"));
  card.appendChild(el("p", "muted settings-card-intro", "Параметры применяются сразу и сохраняются в профиле пользователя Home Assistant."));
  const themeRow = el("label", "settings-toggle-row");
  const themeCopy = el("span", "settings-toggle-copy");
  themeCopy.appendChild(el("strong", null, "Тема панели"));
  themeCopy.appendChild(el("small", null, "Следовать теме Home Assistant, менять тему по времени суток (светлая с 6:00 до 22:00, фон главной тоже дневной или ночной) или использовать постоянную светлую либо тёмную тему."));
  themeRow.appendChild(themeCopy);
  const themeSelect = selectField([
    { value: "auto", label: "Как в Home Assistant" },
    { value: "daynight", label: "День/ночь по времени" },
    { value: "light", label: "Светлая" },
    { value: "dark", label: "Тёмная" },
  ], panel._themeMode, () => {
    panel._themeMode = themeSelect.value;
    panel._persistUserPreferences();
    panel._applyThemeMode();
    panel._render();
  });
  themeSelect.className = "settings-compact-select";
  themeRow.appendChild(themeSelect);
  card.appendChild(themeRow);
  [
    ["large_text", "Крупнее текст и элементы", "Увеличить вторичные подписи и зоны нажатия для настенной панели."],
    ["reduced_motion", "Уменьшить анимацию", "Отключить плавные переходы, если движение отвлекает или устройство работает медленно."],
    ["show_hints", "Показывать пояснения", "Оставить короткие подсказки рядом с настройками, которые влияют на автоматику."],
  ].forEach(([key, title, description]) => {
    const row = el("div", "settings-toggle-row");
    const copy = el("span", "settings-toggle-copy");
    copy.appendChild(el("strong", null, title));
    copy.appendChild(el("small", null, description));
    row.appendChild(copy);
    const enabled = panel._settingsPrefs[key] === true;
    const toggle = el("button", `settings-switch${enabled ? " is-on" : ""}`);
    toggle.type = "button";
    setAttr(toggle, "role", "switch");
    setAttr(toggle, "aria-checked", enabled ? "true" : "false");
    setAttr(toggle, "aria-label", title);
    toggle.appendChild(el("span", "settings-switch-status", enabled ? "Включено" : "Выключено"));
    const track = el("span", "settings-switch-track");
    track.appendChild(el("span", "settings-switch-knob"));
    toggle.appendChild(track);
    toggle.addEventListener("click", () => {
      panel._settingsPrefs[key] = !enabled;
      panel._persistUserPreferences();
      panel._applyLocalPreferences();
      panel._render();
    });
    row.appendChild(toggle);
    card.appendChild(row);
  });
  container.appendChild(card);
  const note = el("section", "card settings-help-card settings-local-note");
  note.appendChild(svgIcon("device"));
  const copy = el("div");
  copy.appendChild(el("strong", null, "Настройки относятся к вашему профилю"));
  copy.appendChild(el("p", "muted", "Тема и доступность будут восстановлены на панелях с тем же пользователем Home Assistant и не меняют устройства или климат."));
  note.appendChild(copy);
  container.appendChild(note);
}
