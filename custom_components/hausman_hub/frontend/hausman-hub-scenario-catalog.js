/* Tablet scenario catalog classification and presentation helpers. */

import { scenarioIconMeta } from "./hausman-hub-scenario-icons.js?v=1.52.163";

export const SCENARIO_FILTERS = [
  ["all", "Все"],
  ["favorite", "На главной"],
  ["enabled", "Включены"],
  ["disabled", "Отключены"],
  ["manual", "Ручные"],
  ["automatic", "Автоматика"],
  ["hybrid", "Гибридные"],
  ["system", "Системные"],
];

const ACTIVATION_LABELS = {
  manual: "Ручной запуск",
  automatic: "Автоматика",
  hybrid: "Автоматика + ручной запуск",
  system: "Системный процесс",
};

const LOGICAL_GROUPS = [
  { id: "home", title: "Режимы дома" },
  { id: "climate", title: "Климат" },
  { id: "media", title: "Медиа" },
  { id: "covers", title: "Шторы" },
];

export function scenarioActivationKind(scenario) {
  const value = String(scenario && scenario.activationKind || "").toLowerCase();
  return Object.hasOwn(ACTIVATION_LABELS, value) ? value : "manual";
}

export function scenarioActivationLabel(scenario) {
  return ACTIVATION_LABELS[scenarioActivationKind(scenario)];
}

export function scenarioDisplayGroup(scenario) {
  if (scenarioActivationKind(scenario) === "system") return "Системные";
  return String(scenario && scenario.group || "Сценарии").trim() || "Сценарии";
}

export function scenarioDisplayText(value, scenario) {
  const text = String(value || "");
  if (scenarioActivationKind(scenario) !== "system") return text;
  return text
    .replace(/Shadow-перенос Node-RED/gi, "Проверочный перенос автоматизации")
    .replace(/Node-RED/gi, "предыдущей системы")
    .replace(/\bShadow\b/gi, "Проверочный режим")
    .replace(/\boff\b/gi, "выключить");
}

export function scenarioLogicalGroup(scenario) {
  const identity = `${scenarioDisplayText(scenario && scenario.title, scenario)} ${scenarioDisplayGroup(scenario)}`.toLocaleLowerCase("ru");
  if (["curtain", "штор"].some((token) => identity.includes(token))) return LOGICAL_GROUPS[3];
  if (["movie", "кино", "media", "медиа", "звук"].some((token) => identity.includes(token))) return LOGICAL_GROUPS[2];
  if (["климат", "comfort", "комфорт", "термостат"].some((token) => identity.includes(token))) return LOGICAL_GROUPS[1];
  if (["режим", "дом", "home"].some((token) => identity.includes(token))) return LOGICAL_GROUPS[0];
  const text = `${scenario && scenario.id || ""} ${identity} ${scenarioDisplayText(scenario && scenario.description, scenario)}`.toLocaleLowerCase("ru");
  if (["curtain", "штор"].some((token) => text.includes(token))) return LOGICAL_GROUPS[3];
  if (["movie", "кино", "media", "звук"].some((token) => text.includes(token))) return LOGICAL_GROUPS[2];
  if (["airing", "quiet", "hot_day", "comfort", "living_night", "living_day", "thermostat", "климат", "комфорт", "проветр", "термоголов"].some((token) => text.includes(token))) return LOGICAL_GROUPS[1];
  return LOGICAL_GROUPS[0];
}

export function scenarioRoomOptions(panel, scenarios) {
  const rooms = panel._homeDashboard && Array.isArray(panel._homeDashboard.rooms)
    ? panel._homeDashboard.rooms : [];
  const names = new Map(rooms.map((room) => [room.id, room.name]));
  const ids = new Set(scenarios.map((scenario) => scenario.roomId).filter((roomId) => names.has(roomId)));
  return rooms.filter((room) => ids.has(room.id)).map((room) => [room.id, room.name]);
}

export function scenarioMatchesCatalog(scenario, state) {
  const kind = scenarioActivationKind(scenario);
  const filter = state.filter || "all";
  if (kind === "system" && filter !== "system") return false;
  if (filter === "system" && kind !== "system") return false;
  if (["manual", "automatic", "hybrid"].includes(filter) && kind !== filter) return false;
  if (filter === "favorite" && scenario.favorite !== true) return false;
  if (filter === "enabled" && scenario.enabled === false) return false;
  if (filter === "disabled" && scenario.enabled !== false) return false;
  if (state.roomId && state.roomId !== "all" && scenario.roomId !== state.roomId) return false;
  const query = String(state.query || "").trim().toLocaleLowerCase("ru");
  if (!query) return true;
  return [scenario.title, scenarioDisplayText(scenario.title, scenario), scenarioDisplayGroup(scenario), scenario.description, scenarioDisplayText(scenario.description, scenario)]
    .some((value) => String(value || "").toLocaleLowerCase("ru").includes(query));
}

export function groupScenarios(scenarios) {
  return LOGICAL_GROUPS.map((group) => ({
    ...group,
    scenarios: scenarios.filter((scenario) => scenarioLogicalGroup(scenario).id === group.id),
  })).filter((group) => group.scenarios.length);
}

function scenarioRuleCount(scenario, key) {
  const items = scenario && scenario.definition && Array.isArray(scenario.definition[key])
    ? scenario.definition[key] : [];
  return items.length;
}

function scenarioCard(panel, source, deps, handlers) {
  const { el, setAttr, svgIcon } = deps;
  const scenario = handlers.normalize(source);
  const title = scenarioDisplayText(scenario.title || scenario.id, scenario);
  const description = scenarioDisplayText(scenario.description, scenario) || "Описание не задано";
  const requiresConfirmation = scenario.requiresConfirmation === true || scenario.requires_confirmation === true;
  const meta = scenarioIconMeta(scenario.icon, title);
  const row = el("article", `scenario-row scenario-library-card${scenario.enabled ? "" : " is-disabled"}`);
  row._scenarioState = scenario;

  const head = el("div", "scenario-library-card-head");
  const icon = el("span", "scenario-icon scenario-library-icon");
  const materialIcon = el("ha-icon", "icon scenario-material-icon");
  setAttr(materialIcon, "icon", `mdi:${meta.mdi}`);
  icon.appendChild(materialIcon);
  head.appendChild(icon);
  const identity = el("div", "scenario-copy scenario-library-identity");
  identity.tabIndex = 0;
  identity.appendChild(el("h3", null, title));
  identity.appendChild(el("small", null, `${scenarioDisplayGroup(scenario)} · ${scenarioActivationLabel(scenario)}`));
  identity.addEventListener("click", () => handlers.open(scenario));
  identity.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handlers.open(scenario);
    }
  });
  head.appendChild(identity);
  const favorite = el("button", `scenario-favorite${scenario.favorite ? " is-active" : ""}`, scenario.favorite ? "★" : "☆");
  favorite.type = "button";
  setAttr(favorite, "aria-label", scenario.favorite ? "Убрать с главного экрана" : "Добавить на главный экран");
  favorite.addEventListener("click", () => {
    scenario.favorite = !scenario.favorite;
    handlers.save(scenario, scenario.favorite ? `Сценарий «${title}» добавлен на главный экран.` : `Сценарий «${title}» убран с главного экрана.`);
  });
  head.appendChild(favorite);
  const menu = el("details", "scenario-more");
  const menuButton = el("summary", null, "•••");
  setAttr(menuButton, "aria-label", `Дополнительные действия для «${title}»`);
  menu.appendChild(menuButton);
  const menuItems = el("div", "scenario-more-menu");
  const menuEdit = el("button", null, "Изменить");
  menuEdit.type = "button";
  menuEdit.addEventListener("click", () => handlers.open(scenario));
  const duplicate = el("button", null, "Создать копию");
  duplicate.type = "button";
  duplicate.addEventListener("click", () => handlers.open(handlers.duplicate(scenario)));
  menuItems.appendChild(menuEdit);
  menuItems.appendChild(duplicate);
  if (!scenario.protected && scenarioActivationKind(scenario) !== "system") {
    const remove = el("button", "is-danger", "Удалить");
    remove.type = "button";
    remove.addEventListener("click", () => handlers.delete(scenario));
    menuItems.appendChild(remove);
  }
  menu.appendChild(menuItems);
  head.appendChild(menu);
  row.appendChild(head);

  const copy = el("div", "scenario-library-copy");
  copy.appendChild(el("p", null, description));
  const summary = el("div", "scenario-library-summary");
  summary.appendChild(svgIcon("bolt"));
  const triggerCount = scenarioRuleCount(scenario, "triggers");
  summary.appendChild(el("span", null, scenario.triggerDescription || `${triggerCount} тригг.`));
  summary.appendChild(el("span", null, `${scenarioRuleCount(scenario, "actions")} действ.`));
  if (requiresConfirmation) summary.appendChild(el("span", "scenario-library-confirmation", "с подтверждением"));
  copy.appendChild(summary);
  row.appendChild(copy);

  const actions = el("div", "scenario-actions scenario-library-actions");
  const enabled = el("button", `scenario-enabled${scenario.enabled ? " is-active" : ""}`);
  enabled.type = "button";
  setAttr(enabled, "role", "switch");
  setAttr(enabled, "aria-checked", String(scenario.enabled));
  setAttr(enabled, "aria-label", scenario.enabled ? "Сценарий включён" : "Сценарий отключён");
  enabled.appendChild(el("span", "scenario-enabled-knob"));
  enabled.addEventListener("click", () => {
    scenario.enabled = !scenario.enabled;
    handlers.save(scenario, `Сценарий «${title}» ${scenario.enabled ? "включён" : "выключен"}.`);
  });
  actions.appendChild(enabled);
  actions.appendChild(el("span", "scenario-library-actions-spacer"));
  const edit = el("button", "secondary scenario-edit", "Изменить");
  edit.type = "button";
  edit.addEventListener("click", () => handlers.open(scenario));
  actions.appendChild(edit);
  const run = el("button", "secondary scenario-run");
  run.type = "button";
  run.disabled = panel._busy || !scenario.enabled;
  run.appendChild(svgIcon("play"));
  run.appendChild(el("span", null, "Запустить"));
  setAttr(run, "aria-label", scenario.enabled ? `Запустить сценарий «${title}»` : `Сценарий «${title}» выключен`);
  run.addEventListener("click", () => panel._post(deps.runApi, { scenario_id: scenario.id }, requiresConfirmation ? `Запустить сценарий «${title}»?` : null));
  actions.appendChild(run);
  row.appendChild(actions);
  return row;
}

export function renderScenarioCatalog(panel, card, sources, deps, handlers) {
  const { el, setAttr, svgIcon } = deps;
  const state = panel._scenarioLibrary;
  const scenarios = sources.map(handlers.normalize);
  const controls = el("div", "scenario-library-controls");
  const search = el("input", "scenario-library-search");
  search.type = "search";
  search.value = state.query;
  setAttr(search, "placeholder", "Найти сценарий");
  setAttr(search, "aria-label", "Найти сценарий");
  controls.appendChild(search);
  const filters = el("div", "scenario-library-filters");
  SCENARIO_FILTERS.forEach(([value, label]) => {
    const button = el("button", state.filter === value ? "is-active" : "", label);
    button.type = "button";
    button.addEventListener("click", () => {
      state.filter = value;
      if (value === "system") state.roomId = "all";
      handlers.refresh();
    });
    filters.appendChild(button);
  });
  controls.appendChild(filters);
  card.appendChild(controls);

  const roomSources = scenarios.filter((scenario) => state.filter === "system"
    ? scenarioActivationKind(scenario) === "system" : scenarioActivationKind(scenario) !== "system");
  const rooms = scenarioRoomOptions(panel, roomSources);
  if (rooms.length) {
    const roomFilters = el("div", "scenario-library-room-filters");
    roomFilters.appendChild(el("span", "scenario-library-filter-label", "Комнаты"));
    [["all", "Все комнаты"], ...rooms].forEach(([value, label]) => {
      const button = el("button", state.roomId === value ? "is-active" : "", label);
      button.type = "button";
      button.addEventListener("click", () => { state.roomId = value; handlers.refresh(); });
      roomFilters.appendChild(button);
    });
    card.appendChild(roomFilters);
  }

  if (!sources.length) {
    const empty = el("div", "scenario-empty");
    const icon = el("span", "scenario-empty-icon");
    icon.appendChild(svgIcon("bolt"));
    empty.appendChild(icon);
    empty.appendChild(el("h3", null, "Создайте первый сценарий"));
    empty.appendChild(el("p", null, "Объедините команды устройств, расписание и условия в одно понятное действие."));
    const create = el("button", "secondary", "Создать сценарий");
    create.addEventListener("click", handlers.create);
    empty.appendChild(create);
    card.appendChild(empty);
    return;
  }

  const baseState = { ...state, query: "" };
  const catalogItems = scenarios.filter((scenario) => scenarioMatchesCatalog(scenario, baseState));
  const empty = el("div", "scenario-empty scenario-empty-compact");
  empty.appendChild(el("h3", null, "Сценарии не найдены"));
  empty.appendChild(el("p", null, "Измените поиск или фильтр."));
  card.appendChild(empty);
  const groupsNode = el("div", "scenario-library-groups");
  const groupNodes = [];
  groupScenarios(catalogItems).forEach((group) => {
    const section = el("section", "scenario-library-section");
    const heading = el("div", "scenario-library-section-heading");
    heading.appendChild(el("h3", null, group.title));
    heading.appendChild(el("span", null, String(group.scenarios.length)));
    section.appendChild(heading);
    const grid = el("div", "scenario-list scenario-library-grid");
    const rows = group.scenarios.map((scenario) => {
      const row = scenarioCard(panel, scenario, deps, handlers);
      grid.appendChild(row);
      return row;
    });
    section.appendChild(grid);
    groupsNode.appendChild(section);
    groupNodes.push({ section, rows });
  });
  card.appendChild(groupsNode);

  const applySearch = () => {
    state.query = search.value;
    let visibleCount = 0;
    groupNodes.forEach(({ section, rows }) => {
      let groupCount = 0;
      rows.forEach((row) => {
        row.hidden = !scenarioMatchesCatalog(row._scenarioState, state);
        if (!row.hidden) { groupCount += 1; visibleCount += 1; }
      });
      section.hidden = groupCount === 0;
    });
    empty.hidden = visibleCount > 0;
    groupsNode.hidden = visibleCount === 0;
  };
  search.addEventListener("input", applySearch);
  applySearch();
}
