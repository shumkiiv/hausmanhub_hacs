import { resolveIntercomQuickAction } from "./hausman-hub-navigation.js?v=1.52.296";
import { apiErrorMessage } from "./hausman-hub-error-taxonomy.js?v=1.52.296";
import { filterCatalogActions } from "./hausman-hub-device-features.js?v=1.52.296";
import { fullDeviceActionRequest, withCorrelationId } from "./hausman-hub-correlation.js?v=1.52.296";
import { coreControlForTarget, coreControlStateOf, withCoreControl } from "./hausman-hub-core-control.js?v=1.52.296";

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
    // The core envelope is captured synchronously from the catalog the
    // user acted on, before any asynchronous work (HH-014.b client).
    // A caller-supplied snapshot (sequential group) always wins so a
    // mid-group reload cannot rewrite confirmed actions. Every
    // recognized transferred target is remembered — ready, unavailable
    // and damaged blocks alike — so losing the block later refuses the
    // command instead of a silent legacy fallback (SEC-HACS-01).
    if (!owner._coreTransferredSeen) owner._coreTransferredSeen = new Set();
    const seen = owner._coreTransferredSeen;
    let corePrepared = null;
    if (options.coreControl !== undefined) {
      corePrepared = options.coreControl;
      if (corePrepared && corePrepared.state) seen.add(targetId);
    } else if (target) {
      if (coreControlStateOf(target) !== null) seen.add(targetId);
      corePrepared = coreControlForTarget(target);
    }
    // Loss of the projection for a previously transferred target is
    // never a fallback to the legacy writer (SEC-HACS-01).
    if (corePrepared === null && seen.has(targetId)) {
      owner._notice = "Ресурс передан ядру, но данные управления потеряны: обновите панель.";
      owner._error = false;
      return;
    }
    if (corePrepared && corePrepared.refused) {
      owner._notice = corePrepared.refused;
      owner._error = false;
      return;
    }
    // The request belongs to the connection it was prepared on: the
    // group loop stops on a session change, and a stale envelope can
    // only end in a server revision conflict — never in a bypass.
    const hass = owner._hass;
    let payload = { targetId, actionId };
    if (value !== null && value !== undefined) payload.value = value;
    if (options.confirmedByUser === true) payload.confirmedByUser = true;
    if (options.dryRun === true) payload.dryRun = true;
    let headers;
    // A transferred target always uses the full form: the legacy form
    // does not allow the coreControl envelope (0.72.1), including for
    // dry runs.
    if (options.dryRun !== true || corePrepared !== null) {
      ({ payload, headers } = fullDeviceActionRequest(payload, deviceActionRequestId()));
      payload = withCoreControl(payload, corePrepared, payload.requestId);
    }
    const receipt = await hass.callApi(
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
