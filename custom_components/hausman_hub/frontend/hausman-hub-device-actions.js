import { resolveIntercomQuickAction } from "./hausman-hub-navigation.js?v=1.52.229";
import { apiErrorMessage } from "./hausman-hub-error-taxonomy.js?v=1.52.229";
import { filterCatalogActions } from "./hausman-hub-device-features.js?v=1.52.229";
import { fullDeviceActionRequest, withCorrelationId } from "./hausman-hub-correlation.js?v=1.52.229";

const DEVICE_ACTION_EXECUTOR_API = "hausman_hub/v1/device-actions";

function deviceActionRequestId() {
  const random = Math.random().toString(36).slice(2, 10);
  return `device-action-${Date.now()}-${random}`.slice(0, 64);
}

export function configuredElectricalBreaker(owner, source, item) {
  const target = item && item.target;
  const action = item && item.action;
  if (!target || !action || !["turn_on", "turn_off"].includes(action.action_id)
      || action.domain !== "switch") return false;
  const physicalId = String(target.physical_id || "");
  const energy = owner._homeDashboard && owner._homeDashboard.energy;
  const configuredIds = new Set(Array.isArray(energy && energy.selectedSourceIds)
    ? energy.selectedSourceIds : []);
  const sourceIds = new Set([source && source.id, source && source.deviceId].filter(Boolean));
  if (!physicalId || !configuredIds.has(physicalId) || !sourceIds.has(physicalId)) return false;
  return target.device_type === "electrical_breaker";
}

export function breakerConfirmation(source, actionId) {
  const effects = {
    turn_on: "Питание подключённой линии будет подано.",
    turn_off: "Питание подключённой линии будет снято.",
  };
  const verbs = { turn_on: "Включить", turn_off: "Отключить" };
  return `${verbs[actionId]} «${source.name}»? ${effects[actionId]}`;
}

export function catalogTargets(owner, device) {
  const catalog = owner._scenarios.catalog && Array.isArray(owner._scenarios.catalog.devices)
    ? owner._scenarios.catalog.devices : [];
  const entityIds = new Set([device.entityId].concat(
    Array.isArray(device.details) ? device.details.map((item) => item.entityId) : []
  ).filter(Boolean));
  const matrix = owner._deviceFeatures && owner._deviceFeatures.matrix;
  const deviceType = String(device && device.domain
    || String(device && device.entityId || "").split(".")[0] || "");
  return catalog.filter((target) => entityIds.has(target.entity_id))
    .map((target) => ({
      ...target,
      actions: filterCatalogActions(matrix, deviceType, target.actions),
    }))
    .filter((target) => target.actions.length);
}

export function deviceActionReceiptText(receipt) {
  const statuses = {
    confirmed: "Применено и подтверждено наблюдением.",
    pending: "Команды отправлены, подтверждение ещё проверяется.",
    partial: "Применено частично.",
    unavailable: "Состояние климатического контура недоступно.",
    up_to_date: "Состояние уже соответствует сохранённому.",
    denied: "Действие отклонено защитой.",
    failed: "Действие не выполнено.",
  };
  const status = receipt && typeof receipt.status === "string" ? receipt.status : "";
  return statuses[status] || `Статус операции: ${status || "неизвестен"}.`;
}

export async function executeDeviceAction(owner, targetId, actionId, value, options = {}) {
  if (owner._busy || !owner._hass) return;
  const configuredIntercom = owner._tabletProfile?.settings?.intercom?.deviceId;
  const catalog = owner._scenarios.catalog && Array.isArray(owner._scenarios.catalog.devices)
    ? owner._scenarios.catalog.devices : [];
  const target = catalog.find((item) => item.target_id === targetId);
  const breaker = target?.device_type === "electrical_breaker";
  if (breaker && actionId === "toggle") {
    owner._notice = "Для автомата доступны только явные команды включения и отключения.";
    owner._render();
    return;
  }
  if (breaker && ["turn_on", "turn_off"].includes(actionId)
      && options.confirmedByUser !== true && options.dryRun !== true) {
    if (typeof globalThis.confirm !== "function"
        || !globalThis.confirm(breakerConfirmation(target, actionId))) {
      owner._notice = "Команда автомату отменена.";
      owner._render();
      return;
    }
    options = { ...options, confirmedByUser: true };
  }
  const intercom = configuredIntercom
    ? resolveIntercomQuickAction(owner._homeDevices("devices"), catalog, configuredIntercom)
    : null;
  const isIntercom = intercom?.targetId === targetId && intercom?.actionId === actionId;
  if (isIntercom && options.confirmedByUser !== true && options.dryRun !== true) {
    if (typeof globalThis.confirm !== "function"
        || !globalThis.confirm("Открыть дверь домофона?")) {
      owner._notice = "Открытие домофона отменено.";
      owner._render();
      return;
    }
    options = { ...options, confirmedByUser: true };
  }
  owner._busy = true;
  owner._notice = "";
  owner._render();
  try {
    let payload = { targetId, actionId };
    if (value !== null && value !== undefined) payload.value = value;
    if (options.confirmedByUser === true) payload.confirmedByUser = true;
    if (options.dryRun === true) payload.dryRun = true;
    let headers;
    if (options.dryRun !== true) {
      ({ payload, headers } = fullDeviceActionRequest(payload, deviceActionRequestId()));
    }
    const receipt = await owner._hass.callApi(
      "POST",
      DEVICE_ACTION_EXECUTOR_API,
      withCorrelationId(DEVICE_ACTION_EXECUTOR_API, payload),
      headers,
    );
    owner._notice = deviceActionReceiptText(receipt);
    owner._error = false;
  } catch (error) {
    owner._notice = apiErrorMessage(error);
    owner._error = false;
  } finally {
    owner._busy = false;
    await owner._load();
  }
}
