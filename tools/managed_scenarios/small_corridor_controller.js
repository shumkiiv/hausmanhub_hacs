// HAUSMAN_MANAGED_SCENARIO system-small-corridor-light-controller
// Малый коридор: один таймер движения, плавное выключение и ночная блокировка.
const started = Date.now();
const request = (msg.payload && typeof msg.payload === 'object') ? msg.payload : {};
const inputs = (request.inputs && typeof request.inputs === 'object') ? request.inputs : {};
const trigger = (request.context && request.context.trigger && typeof request.context.trigger === 'object') ? request.context.trigger : {};
const ID = Object.freeze({motion: 'entity_90417aada6a33491', sun: 'entity_6b9ccdab9bb484b2',
  outsideLux: 'entity_5f3b4436fb7b6f2b', localLight: 'entity_c9d6bc67f172f30d',
  relay: 'entity_4be32416634e6416', chandelier: 'entity_9ed909332fdaa8fd'});
function item(targetId) { const value = inputs[targetId]; return value && typeof value === 'object' ? value : {state: null, attributes: {}}; }
function state(targetId) { const value = item(targetId).state; return value === null || value === undefined ? null : String(value); }
function numeric(value) { if (value === null || value === undefined || value === '') return null; const number = Number(value); return Number.isFinite(number) ? number : null; }
function numberAttribute(targetId, name) { return numeric(item(targetId).attributes && item(targetId).attributes[name]); }
function deviceAction(id, targetId, targetName, actionId, value) {
  const result = {id, type: 'device_action', targetId, targetName, actionId};
  if (value !== undefined) result.value = value;
  return result;
}
function profileActions(brightness, kelvin, manual = false) {
  const actions = [];
  if (manual) actions.push(deviceAction('relay_on', ID.relay, 'Выключатель малого коридора', 'turn_on'));
  actions.push(
    deviceAction('chandelier_on', ID.chandelier, 'Люстра малого коридора', 'turn_on'),
    {id: 'ownership_wait', type: 'delay', delaySeconds: 1},
  );
  const rawBrightness = numberAttribute(ID.chandelier, 'brightness');
  const percent = rawBrightness === null ? null : Math.round(rawBrightness * 100 / 255);
  const directKelvin = numberAttribute(ID.chandelier, 'color_temp_kelvin');
  const mired = numberAttribute(ID.chandelier, 'color_temp');
  const currentKelvin = directKelvin !== null ? directKelvin : mired !== null && mired > 0 ? Math.round(1000000 / mired) : null;
  if (state(ID.chandelier) !== 'on' || percent === null || Math.abs(percent - brightness) > 1) actions.push(deviceAction('brightness', ID.chandelier, 'Люстра малого коридора', 'set_brightness_percent', brightness));
  if (state(ID.chandelier) !== 'on' || currentKelvin === null || Math.abs(currentKelvin - kelvin) > 25) actions.push(deviceAction('temperature', ID.chandelier, 'Люстра малого коридора', 'set_color_temperature', kelvin));
  return actions;
}
function fadeActions(waitSeconds) {
  if (state(ID.relay) !== 'on') return [];
  const actions = [];
  if (waitSeconds > 0) actions.push({id: 'absence_wait', type: 'delay', delaySeconds: waitSeconds});
  const rawBrightness = numberAttribute(ID.chandelier, 'brightness');
  const currentPercent = rawBrightness === null ? 101 : Math.round(rawBrightness * 100 / 255);
  for (const percent of [75, 50, 25, 5]) {
    if (percent >= currentPercent) continue;
    actions.push(deviceAction(`fade_${percent}`, ID.chandelier, 'Люстра малого коридора', 'set_brightness_percent', percent));
    actions.push({id: `fade_wait_${percent}`, type: 'delay', delaySeconds: 5});
  }
  actions.push(deviceAction('chandelier_off', ID.chandelier, 'Люстра малого коридора', 'turn_off'));
  actions.push(deviceAction('relay_off', ID.relay, 'Выключатель малого коридора', 'turn_off'));
  return actions;
}
const timestampMs = Number(request.context && request.context.timestampMs) || Date.now();
const local = new Date(timestampMs + 6 * 60 * 60 * 1000);
const hour = local.getUTCHours();
const motionState = state(ID.motion);
const sunState = state(ID.sun);
const outsideLux = numeric(state(ID.outsideLux));
const localLight = state(ID.localLight);
const nightBlocked = sunState === 'below_horizon' && hour >= 0 && hour < 12;
const triggerId = String(trigger.trigger_id || '');
const manualChandelier = trigger.source === 'manual' && triggerId === 'manual_chandelier_on';
const midnightCutoff = triggerId === 'midnight_cutoff';
let branch = 'motion_uncertain';
let brightness = null;
let kelvin = null;
let actions = [];
if (manualChandelier) {
  [branch, brightness, kelvin] = ['manual_chandelier', 100, 3000]; actions = profileActions(brightness, kelvin, true);
} else if (midnightCutoff) {
  branch = 'midnight_cutoff'; actions = fadeActions(0);
} else if (motionState === 'off' && triggerId === 'motion_changed') {
  branch = 'fade_after_5m'; actions = fadeActions(300);
} else if (motionState === 'on' && nightBlocked) {
  branch = 'night_blocked_until_sunrise';
} else if (motionState === 'on' && sunState === 'above_horizon') {
  [branch, brightness, kelvin] = ['day', 100, 3000]; actions = profileActions(brightness, kelvin);
} else if (motionState === 'on' && sunState === 'below_horizon') {
  if (outsideLux === null) [branch, brightness, kelvin] = ['evening_fallback', 30, 2700];
  else if (outsideLux < 20) [branch, brightness, kelvin] = localLight === 'bright' ? ['night_bright', 20, 2400] : ['night_dark', 10, 2200];
  else if (outsideLux < 100) [branch, brightness, kelvin] = localLight === 'bright' ? ['late_bright', 30, 2700] : ['late_dark', 20, 2400];
  else [branch, brightness, kelvin] = localLight === 'bright' ? ['dusk_bright', 45, 3000] : ['dusk_dark', 35, 2700];
  actions = profileActions(brightness, kelvin);
}
const trace = [
  {id: 'motion', title: 'Движение в малом коридоре', status: motionState === 'on' ? 'passed' : motionState === 'off' ? 'failed' : 'skipped', actual: motionState, expected: 'on', reason: motionState === null ? 'Нет достоверного состояния.' : null},
  {id: 'night_block', title: 'Блокировка 00:00-рассвет', status: nightBlocked ? 'selected' : 'skipped', actual: `${String(hour).padStart(2, '0')}:xx; ${sunState || 'sun unavailable'}`, expected: 'автовключение запрещено только после полуночи до рассвета', reason: null},
  {id: 'profile', title: 'Профиль малого коридора', status: 'selected', actual: outsideLux, expected: branch, reason: null},
];
msg.statusCode = 200;
msg.headers = {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'};
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(request.correlationId || ''),
  scenarioId: 'system-small-corridor-light-controller', status: actions.length ? 'completed' : 'skipped',
  summary: actions.length ? `Малый коридор: выбрана ветка ${branch}.` : 'Малый коридор: команды не требуются.',
  selectedBranch: branch, durationMs: Math.max(0, Date.now() - started), trace, actions};
return msg;
