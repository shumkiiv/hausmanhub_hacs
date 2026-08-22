const UTILITY_CARD_MODE_STORAGE_KEY = "hausman.overview.card-modes.v1";
const UTILITY_CARD_KEYS = new Set(["energy", "lighting"]);

function utilityValidNumber(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

function utilityCompactNumber(value, digits = 1) {
  if (!utilityValidNumber(value)) return "—";
  return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

function utilityPlural(count, one, few, many) {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

function utilityCardModes(panel) {
  if (panel._overviewCardModeInitialized === true
    && panel._overviewCardModes && typeof panel._overviewCardModes === "object") {
    return panel._overviewCardModes;
  }
  let saved = panel._overviewCardModes;
  if (!saved || typeof saved !== "object") {
    try {
      const raw = globalThis.localStorage?.getItem(UTILITY_CARD_MODE_STORAGE_KEY);
      saved = raw ? JSON.parse(raw) : null;
    } catch (error) {
      saved = null;
    }
  }
  const mode = saved?.mode === "expanded" || saved?.energy === "expanded"
    || saved?.lighting === "expanded" ? "expanded" : "compact";
  panel._overviewCardModes = {
    energy: mode,
    lighting: mode,
  };
  panel._overviewCardModeInitialized = true;
  return panel._overviewCardModes;
}

export function overviewCardMode(panel, cardKey) {
  if (!UTILITY_CARD_KEYS.has(cardKey)) return "compact";
  return utilityCardModes(panel)[cardKey] === "expanded" ? "expanded" : "compact";
}

function utilityPersistCardModes(panel) {
  try {
    globalThis.localStorage?.setItem(UTILITY_CARD_MODE_STORAGE_KEY,
      JSON.stringify(utilityCardModes(panel)));
  } catch (error) {
    // Browser storage is optional: the in-memory mode remains available.
  }
}

function utilityModeToggle(panel, card, cardKey, cardLabel, deps) {
  const button = deps.el("button", "overview-tablet-card-mode");
  button.type = "button";
  const copy = deps.el("span");
  button.appendChild(copy);
  button.appendChild(deps.svgIcon("chevron-right"));
  const apply = (mode) => {
    const expanded = mode === "expanded";
    card.classList.toggle("is-expanded", expanded);
    copy.textContent = expanded ? "Свернуть" : "Развернуть";
    deps.setAttr(button, "aria-expanded", expanded ? "true" : "false");
    deps.setAttr(button, "aria-label", `${expanded ? "Свернуть" : "Развернуть"} панель «${cardLabel}»`);
    deps.setAttr(button, "title", expanded ? "Компактный режим" : "Развёрнутый режим");
  };
  apply(overviewCardMode(panel, cardKey));
  button.addEventListener("click", (event) => {
    event?.stopPropagation?.();
    const modes = utilityCardModes(panel);
    const next = modes[cardKey] === "expanded" ? "compact" : "expanded";
    modes.energy = next;
    modes.lighting = next;
    utilityPersistCardModes(panel);
    panel._overviewUtilityApplyMode?.(next);
  });
  return { button, apply };
}

function utilityEnergySourceValue(source, units) {
  const power = utilityValidNumber(source.currentPowerW)
    ? `${utilityCompactNumber(source.currentPowerW, 0)} Вт` : null;
  const current = utilityValidNumber(source.currentA) ? `${Number(source.currentA).toLocaleString("ru-RU", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })} А` : null;
  if (units === "amps") return current || "Нет данных";
  if (units === "both") return [power, current].filter(Boolean).join(" · ") || "Нет данных";
  return power || (utilityValidNumber(source.todayKwh)
    ? `${utilityCompactNumber(source.todayKwh)} кВт·ч` : "Нет данных");
}

function utilitySelectedEnergySources(energy) {
  const sources = Array.isArray(energy?.sources) ? energy.sources : [];
  if (energy?.settings?.useAllDevices === true) return sources;
  const selected = new Set(Array.isArray(energy?.selectedSourceIds) ? energy.selectedSourceIds : []);
  return sources.filter((source) => selected.has(source.id) || selected.has(source.deviceId));
}

function utilityMetric(deps, label, value, tone = "") {
  const metric = deps.el("span", `overview-tablet-energy-metric${tone ? ` is-${tone}` : ""}`);
  metric.appendChild(deps.el("small", null, label));
  metric.appendChild(deps.el("strong", null, value));
  return metric;
}

function utilityEnergyValue(source, key, unit, digits = 1) {
  return utilityValidNumber(source?.[key])
    ? `${utilityCompactNumber(source[key], digits)} ${unit}` : "—";
}

function utilityRenderExpandedEnergy(panel, card, energy, sources, deps) {
  const expanded = deps.el("div", "overview-tablet-energy-expanded");
  const metrics = deps.el("div", "overview-tablet-energy-metrics");
  metrics.appendChild(utilityMetric(deps, "Мощность",
    utilityEnergyValue(energy, "currentPowerW", "Вт", 0), "accent"));
  metrics.appendChild(utilityMetric(deps, "Ток", utilityEnergyValue(energy, "currentA", "А", 2)));
  if (energy.settings?.showVoltage !== false) {
    metrics.appendChild(utilityMetric(deps, "Напряжение",
      utilityEnergyValue(energy, "voltageV", "В", 1)));
  }
  metrics.appendChild(utilityMetric(deps, "Накоплено",
    utilityEnergyValue(energy, "totalKwh", "кВт·ч", 2)));
  expanded.appendChild(metrics);
  const sourceList = deps.el("div", "overview-tablet-energy-expanded-list");
  if (!sources.length) {
    sourceList.appendChild(deps.el("div", "overview-tablet-energy-empty", "Нет выбранных источников"));
  } else {
    sources.slice(0, 6).forEach((source) => {
      const item = deps.el("button", "overview-tablet-energy-expanded-source");
      item.type = "button";
      item.addEventListener("click", () => panel._activateSection("energy"));
      const identity = deps.el("span", "overview-tablet-energy-expanded-copy");
      const name = deps.el("span", "overview-tablet-energy-name");
      name.appendChild(deps.el("strong", null, source.name || "Источник энергии"));
      const availability = deps.el("i", source.available === false ? "is-offline" : "is-online");
      deps.setAttr(availability, "aria-label", source.available === false ? "Нет связи" : "В сети");
      name.appendChild(availability);
      identity.appendChild(name);
      identity.appendChild(deps.el("small", null,
        `${source.roomName || "Дом"} · ${source.available === false ? "нет связи" : "в сети"}`));
      item.appendChild(identity);
      const sourceMetrics = deps.el("span", "overview-tablet-energy-expanded-values");
      sourceMetrics.appendChild(deps.el("span", null,
        utilityEnergyValue(source, "currentPowerW", "Вт", 0)));
      sourceMetrics.appendChild(deps.el("span", null,
        utilityEnergyValue(source, "currentA", "А", 2)));
      if (energy.settings?.showVoltage !== false) {
        sourceMetrics.appendChild(deps.el("span", null,
          utilityEnergyValue(source, "voltageV", "В", 1)));
      }
      sourceMetrics.appendChild(deps.el("span", null,
        utilityEnergyValue(source, "totalKwh", "кВт·ч", 2)));
      item.appendChild(sourceMetrics);
      item.appendChild(deps.svgIcon("chevron-right"));
      sourceList.appendChild(item);
    });
    if (sources.length > 6) {
      sourceList.appendChild(deps.el("span", "overview-tablet-energy-more", `+${sources.length - 6}`));
    }
  }
  expanded.appendChild(sourceList);
  card.appendChild(expanded);
}

function utilityRenderEnergy(panel, dashboard, deps) {
  const energy = dashboard.energy || {};
  const units = String(energy.settings?.displayUnits || "watts").toLowerCase();
  const sources = utilitySelectedEnergySources(energy);
  const card = deps.el("section", "overview-tablet-bottom-card is-energy");
  const head = deps.el("div", "overview-tablet-bottom-head");
  const title = deps.el("h2");
  title.appendChild(deps.svgIcon("energy"));
  title.appendChild(deps.el("span", null, "Показания энергии"));
  head.appendChild(title);
  const actions = deps.el("div", "overview-tablet-bottom-actions");
  const modeControl = utilityModeToggle(panel, card, "energy", "Показания энергии", deps);
  actions.appendChild(modeControl.button);
  const settings = deps.el("button", null, "Настройки");
  settings.type = "button";
  settings.appendChild(deps.svgIcon("chevron-right"));
  settings.addEventListener("click", () => panel._activateSection("energy"));
  actions.appendChild(settings);
  head.appendChild(actions);
  card.appendChild(head);
  const list = deps.el("div", "overview-tablet-energy-sources overview-tablet-energy-compact");
  if (!sources.length) {
    list.appendChild(deps.el("div", "overview-tablet-energy-empty", "Нет выбранных источников"));
  } else {
    sources.slice(0, 2).forEach((source) => {
      const item = deps.el("button", "overview-tablet-energy-source");
      item.type = "button";
      item.addEventListener("click", () => panel._activateSection("energy"));
      const name = deps.el("span", "overview-tablet-energy-name");
      name.appendChild(deps.el("strong", null, source.name || "Источник энергии"));
      name.appendChild(deps.el("i", source.available === false ? "is-offline" : "is-online"));
      item.appendChild(name);
      item.appendChild(deps.el("span", "overview-tablet-energy-value",
        utilityEnergySourceValue(source, units)));
      list.appendChild(item);
    });
    if (sources.length > 2) {
      list.appendChild(deps.el("span", "overview-tablet-energy-more", `+${sources.length - 2}`));
    }
  }
  card.appendChild(list);
  utilityRenderExpandedEnergy(panel, card, energy, sources, deps);
  card._overviewUtilityModeControl = modeControl;
  return card;
}

function utilityPhysicalDevices(devices) {
  const byPhysicalId = new Map();
  devices.forEach((device) => {
    const key = device.physicalId || device.id;
    if (!key || byPhysicalId.has(key)) return;
    byPhysicalId.set(key, device);
  });
  return [...byPhysicalId.values()];
}

function utilityLightAvailable(device) {
  return device?.unavailable !== true && device?.state !== "unavailable";
}

function utilityLightActive(device) {
  return utilityLightAvailable(device) && device?.active === true;
}

function utilityRenderExpandedLighting(panel, card, lights, deps) {
  const expanded = deps.el("div", "overview-tablet-lighting-expanded");
  const active = lights.filter(utilityLightActive).length;
  const offline = lights.filter((device) => !utilityLightAvailable(device)).length;
  const metrics = deps.el("div", "overview-tablet-lighting-metrics");
  metrics.appendChild(utilityMetric(deps, "Включено", `${active} из ${lights.length}`, "lighting"));
  metrics.appendChild(utilityMetric(deps, "Без связи", String(offline), offline ? "warning" : ""));
  expanded.appendChild(metrics);
  const list = deps.el("div", "overview-tablet-lighting-list");
  const ordered = [...lights].sort((left, right) => {
    const activeOrder = Number(utilityLightActive(right)) - Number(utilityLightActive(left));
    if (activeOrder) return activeOrder;
    const offlineOrder = Number(!utilityLightAvailable(left)) - Number(!utilityLightAvailable(right));
    if (offlineOrder) return offlineOrder;
    return String(left.roomName || left.name || "")
      .localeCompare(String(right.roomName || right.name || ""), "ru");
  });
  if (!ordered.length) {
    list.appendChild(deps.el("div", "overview-tablet-energy-empty", "Освещение не найдено"));
  } else {
    ordered.slice(0, 6).forEach((device) => {
      const item = deps.el("button", "overview-tablet-lighting-device");
      item.type = "button";
      item.addEventListener("click", () => panel._activateSection("lighting"));
      const icon = deps.el("span", `overview-tablet-lighting-device-icon${utilityLightActive(device) ? " is-on" : ""}${utilityLightAvailable(device) ? "" : " is-offline"}`);
      icon.appendChild(deps.svgIcon("lightbulb"));
      item.appendChild(icon);
      const copy = deps.el("span", "overview-tablet-lighting-device-copy");
      copy.appendChild(deps.el("strong", null, device.name || "Светильник"));
      copy.appendChild(deps.el("small", null, device.roomName || "Без комнаты"));
      item.appendChild(copy);
      item.appendChild(deps.el("span", "overview-tablet-lighting-device-state",
        utilityLightAvailable(device)
          ? (device.stateLabel || (utilityLightActive(device) ? "Включено" : "Выключено"))
          : "Нет связи"));
      item.appendChild(deps.svgIcon("chevron-right"));
      list.appendChild(item);
    });
    if (ordered.length > 6) {
      list.appendChild(deps.el("span", "overview-tablet-energy-more", `+${ordered.length - 6}`));
    }
  }
  expanded.appendChild(list);
  card.appendChild(expanded);
}

function utilityRenderLighting(panel, dashboard, deps) {
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const lights = utilityPhysicalDevices(devices.filter((device) =>
    device.domain === "light" || device.category === "lighting"));
  const active = lights.filter(utilityLightActive).length;
  const card = deps.el("section", "overview-tablet-bottom-card is-lighting");
  const head = deps.el("div", "overview-tablet-bottom-head");
  const title = deps.el("h2");
  title.appendChild(deps.svgIcon("lightbulb"));
  title.appendChild(deps.el("span", null, "Освещение"));
  head.appendChild(title);
  const modeControl = utilityModeToggle(panel, card, "lighting", "Освещение", deps);
  head.appendChild(modeControl.button);
  card.appendChild(head);
  const compact = deps.el("button", "overview-tablet-lighting-compact");
  compact.type = "button";
  compact.addEventListener("click", () => panel._activateSection("lighting"));
  const bulbs = deps.el("span", "overview-tablet-light-bulbs");
  const visible = active ? Math.min(active, 3) : 1;
  for (let index = 0; index < visible; index += 1) bulbs.appendChild(deps.svgIcon("lightbulb"));
  compact.appendChild(bulbs);
  compact.appendChild(deps.el("span", "overview-tablet-light-label",
    active ? `${active} сейчас ${utilityPlural(active, "горит", "горят", "горят")}` : "Свет выключен"));
  card.appendChild(compact);
  utilityRenderExpandedLighting(panel, card, lights, deps);
  card._overviewUtilityModeControl = modeControl;
  return card;
}

export function renderOverviewUtilityCards(panel, container, dashboard, deps) {
  const row = deps.el("div", "overview-tablet-bottom-grid");
  const cards = [
    utilityRenderEnergy(panel, dashboard, deps),
    utilityRenderLighting(panel, dashboard, deps),
  ];
  cards.forEach((card) => row.appendChild(card));
  const applyMode = (mode) => {
    const expanded = mode === "expanded";
    row.classList.toggle("is-expanded", expanded);
    container.parentElement?.classList?.toggle("overview-utility-expanded", expanded);
    cards.forEach((card) => card._overviewUtilityModeControl?.apply(mode));
  };
  panel._overviewUtilityApplyMode = applyMode;
  applyMode(overviewCardMode(panel, "energy"));
  container.appendChild(row);
}
