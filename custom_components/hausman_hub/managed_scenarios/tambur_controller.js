// HAUSMAN_MANAGED_SCENARIO system-tambur-adaptive-controller
// Тамбур: единый профиль люстры и точек, входная дверь, ручной приоритет и зеркало 23:00-01:00.
const started = Date.now();
const request = (msg.payload && typeof msg.payload === 'object') ? msg.payload : {};
const inputs = (request.inputs && typeof request.inputs === 'object') ? request.inputs : {};
const trigger = (request.context && request.context.trigger && typeof request.context.trigger === 'object')
  ? request.context.trigger
  : {};

const ID = Object.freeze({
  presence: 'entity_156050daca86aa6c',
  motion: 'entity_10b78187426f8485',
  sun: 'entity_6b9ccdab9bb484b2',
  outsideLux: 'entity_5f3b4436fb7b6f2b',
  chandelier: 'entity_71859313239a14e4',
  points: 'entity_cd0098e5ff95da46',
  mirror: 'entity_fbdf27871edb89bf',
  chandelierPower: 'entity_b47991988cc6b9f3',
  entryDoor: 'entity_170c7a4e2505b803',
});

function item(targetId) {
  const value = inputs[targetId];
  return value && typeof value === 'object' ? value : {state: null, attributes: {}};
}
function state(targetId) {
  const value = item(targetId).state;
  return value === null || value === undefined ? null : String(value);
}
function numeric(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
function numberAttribute(targetId, name) {
  return numeric(item(targetId).attributes && item(targetId).attributes[name]);
}
function switchAction(id, targetId, targetName, turnOn) {
  return {id, type: 'device_action', targetId, targetName,
    actionId: turnOn ? 'turn_on' : 'turn_off', actionTitle: turnOn ? 'Включить' : 'Выключить'};
}
function lightAction(id, actionId, value) {
  return {id, type: 'device_action', targetId: ID.chandelier, targetName: 'Люстра тамбур',
    actionId, actionTitle: actionId === 'set_brightness_percent' ? 'Яркость' : 'Температура света', value};
}
function profileActions(brightness, kelvin, includePoints = true) {
  const actions = [
    switchAction('chandelier_on', ID.chandelier, 'Люстра тамбур', true),
    {id: 'chandelier_ownership_wait', type: 'delay', delaySeconds: 1},
  ];
  const chandelierOn = state(ID.chandelier) === 'on';
  const rawBrightness = numberAttribute(ID.chandelier, 'brightness');
  const currentPercent = rawBrightness === null ? null : Math.round(rawBrightness * 100 / 255);
  const directKelvin = numberAttribute(ID.chandelier, 'color_temp_kelvin');
  const mired = numberAttribute(ID.chandelier, 'color_temp');
  const currentKelvin = directKelvin !== null ? directKelvin : mired !== null && mired > 0 ? Math.round(1000000 / mired) : null;
  if (!chandelierOn || currentPercent === null || Math.abs(currentPercent - brightness) > 1) {
    actions.push(lightAction('brightness', 'set_brightness_percent', brightness));
  }
  if (!chandelierOn || currentKelvin === null || Math.abs(currentKelvin - kelvin) > 25) {
    actions.push(lightAction('temperature_target', 'set_color_temperature', kelvin));
  }
  if (includePoints) actions.push(switchAction('points_on', ID.points, 'Точки тамбура', true));
  return actions;
}
function fadeActions(waitSeconds) {
  const actions = [];
  const chandelierOn = state(ID.chandelierPower) === 'on' && state(ID.chandelier) === 'on';
  const pointsOn = state(ID.points) === 'on';
  if (!chandelierOn && !pointsOn) return actions;
  if (waitSeconds > 0) actions.push({id: 'absence_wait', type: 'delay', delaySeconds: waitSeconds});
  if (chandelierOn) {
    const rawBrightness = numberAttribute(ID.chandelier, 'brightness');
    const currentPercent = rawBrightness === null ? 101 : Math.round(rawBrightness * 100 / 255);
    for (const percent of [75, 50, 25, 5]) {
      if (percent >= currentPercent) continue;
      actions.push(lightAction(`fade_${percent}`, 'set_brightness_percent', percent));
      actions.push({id: `fade_wait_${percent}`, type: 'delay', delaySeconds: 5});
    }
    actions.push(switchAction('chandelier_off', ID.chandelier, 'Люстра тамбур', false));
  }
  if (pointsOn) actions.push(switchAction('points_off', ID.points, 'Точки тамбура', false));
  return actions;
}

const timestampMs = Number(request.context && request.context.timestampMs) || Date.now();
const local = new Date(timestampMs + 6 * 60 * 60 * 1000);
const hour = local.getUTCHours();
const clock = `${String(hour).padStart(2, '0')}:${String(local.getUTCMinutes()).padStart(2, '0')}`;
const presenceState = state(ID.presence);
const motionState = state(ID.motion);
const entryDoorState = state(ID.entryDoor);
const triggerId = String(trigger.trigger_id || '');
const entryDoorOpened = triggerId === 'entry_door_unlocked' && entryDoorState === 'unlocked';
const occupied = presenceState === 'on' || motionState === 'on' || entryDoorOpened;
const confidentlyAbsent = presenceState === 'off' && motionState === 'off';
const sunState = state(ID.sun);
const outsideLux = numeric(state(ID.outsideLux));
const manualChandelier = trigger.source === 'manual' && triggerId === 'manual_chandelier_on';
const typedGroupAction = trigger.source === 'manual' &&
  trigger.binding === 'tambur-light-group' &&
  ['on', 'off', 'toggle'].includes(String(trigger.typed_intent || '')) &&
  ['on', 'off'].includes(String(trigger.direct_user_intent || ''));

let branch = 'presence_uncertain';
let brightness = null;
let kelvin = null;
let actions = [];
if (typedGroupAction) {
  const chandelier = state(ID.chandelier);
  const points = state(ID.points);
  const known = (value) => value === 'on' || value === 'off';
  const intent = String(trigger.direct_user_intent);
  const turnOff = intent === 'off';
  branch = `direct_user_group_${String(trigger.typed_intent)}_${intent}`;
  if (turnOff) {
    actions.push(switchAction('chandelier_off', ID.chandelier, 'Люстра тамбур', false));
    actions.push(switchAction('points_off', ID.points, 'Точки тамбура', false));
  } else if (known(chandelier) && known(points)) {
      if (chandelier === 'off') actions.push(switchAction('chandelier_on', ID.chandelier, 'Люстра тамбур', true));
      if (points === 'off') actions.push(switchAction('points_on', ID.points, 'Точки тамбура', true));
  }
} else if (manualChandelier) {
  branch = 'manual_chandelier';
  brightness = 100;
  kelvin = 3000;
  actions = profileActions(brightness, kelvin, false);
} else if (triggerId === 'mirror_window_start' || triggerId === 'mirror_window_midnight') {
  branch = 'mirror_23_01_on';
  if (state(ID.mirror) !== 'on') actions.push(switchAction('mirror_on', ID.mirror, 'Подсветка зеркала тамбура', true));
} else if (triggerId === 'mirror_window_end') {
  branch = 'mirror_01_off';
  if (state(ID.mirror) === 'on') actions.push(switchAction('mirror_off', ID.mirror, 'Подсветка зеркала тамбура', false));
} else if (confidentlyAbsent && (triggerId === 'presence_changed' || triggerId === 'motion_changed')) {
  branch = 'fade_after_10m';
  actions = fadeActions(600);
} else if (occupied && sunState === 'above_horizon') {
  branch = entryDoorOpened ? 'entry_sunrise_to_sunset' : 'sunrise_to_sunset';
  brightness = 100;
  kelvin = 3000;
  actions = profileActions(brightness, kelvin);
} else if (occupied && sunState === 'below_horizon') {
  if (outsideLux === null) [branch, brightness, kelvin] = ['after_sunset_lux_fallback', 50, 4400];
  else if (outsideLux < 20) [branch, brightness, kelvin] = ['after_sunset_dark', 85, 6500];
  else if (outsideLux < 100) [branch, brightness, kelvin] = ['after_sunset_low', 70, 5200];
  else if (outsideLux < 400) [branch, brightness, kelvin] = ['after_sunset_dusk', 50, 4400];
  else [branch, brightness, kelvin] = ['after_sunset_bright', 35, 3600];
  if (entryDoorOpened) branch = `entry_${branch}`;
  actions = profileActions(brightness, kelvin);
}

const trace = [
  {id: 'entry_door', title: 'Открытие входной двери', status: entryDoorOpened ? 'selected' : 'skipped',
    actual: entryDoorState, expected: 'unlocked', reason: null},
  {id: 'presence', title: 'Присутствие в тамбуре', status: occupied ? 'passed' : confidentlyAbsent ? 'failed' : 'skipped',
    actual: `${presenceState || 'unknown'}; ${motionState || 'unknown'}`, expected: 'датчик on или входная дверь unlocked',
    reason: !occupied && !confidentlyAbsent ? 'Состояние одного из датчиков недостоверно.' : null},
  {id: 'time_band', title: 'Граница времени суток', status: 'selected', actual: `${clock}; ${sunState || 'sun unavailable'}`, expected: branch, reason: null},
  {id: 'outside_light', title: 'Освещённость на улице', status: outsideLux === null ? 'skipped' : 'selected',
    actual: outsideLux, expected: 'чем темнее, тем ярче и теплее', reason: outsideLux === null ? 'Использован безопасный вечерний профиль.' : null},
  {id: 'manual_priority', title: 'Ручной приоритет люстры', status: manualChandelier ? 'selected' : 'skipped', actual: trigger.source || null, expected: 'manual', reason: null},
];
msg.statusCode = 200;
msg.headers = {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'};
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1},
  correlationId: String(request.correlationId || ''), scenarioId: 'system-tambur-adaptive-controller',
  status: actions.length ? 'completed' : 'skipped',
  summary: actions.length ? `Тамбур: выбрана ветка ${branch}.` : 'Тамбур: команды не требуются.',
  selectedBranch: branch, durationMs: Math.max(0, Date.now() - started), trace, actions};
return msg;
