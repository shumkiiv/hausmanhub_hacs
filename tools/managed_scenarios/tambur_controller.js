// HAUSMAN_MANAGED_SCENARIO system-tambur-adaptive-controller
// Тамбур: два датчика присутствия, 10 минут уверенного отсутствия и профиль 30 минут.
const started = Date.now();
const request = (msg.payload && typeof msg.payload === 'object') ? msg.payload : {};
const inputs = (request.inputs && typeof request.inputs === 'object') ? request.inputs : {};

const ID = Object.freeze({
  presence: 'entity_156050daca86aa6c',
  motion: 'entity_10b78187426f8485',
  sun: 'entity_6b9ccdab9bb484b2',
  chandelier: 'entity_71859313239a14e4',
  mirror: 'entity_fbdf27871edb89bf',
});

function item(targetId) {
  const value = inputs[targetId];
  return value && typeof value === 'object' ? value : {state: null, attributes: {}};
}

function state(targetId) {
  const value = item(targetId).state;
  return value === null || value === undefined ? null : String(value);
}

function switchAction(id, targetId, targetName, turnOn) {
  return {
    id,
    type: 'device_action',
    targetId,
    targetName,
    actionId: turnOn ? 'turn_on' : 'turn_off',
    actionTitle: turnOn ? 'Включить' : 'Выключить',
  };
}

function lightAction(id, actionId, value) {
  return {
    id,
    type: 'device_action',
    targetId: ID.chandelier,
    targetName: 'Люстра тамбур',
    actionId,
    actionTitle: actionId === 'set_brightness_percent' ? 'Яркость' : 'Температура света',
    value,
  };
}

const timestampMs = Number(request.context && request.context.timestampMs) || Date.now();
const local = new Date(timestampMs + 6 * 60 * 60 * 1000);
const hour = local.getUTCHours();
const minute = local.getUTCMinutes();
const clockMinutes = hour * 60 + minute;
const clock = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
const presenceState = state(ID.presence);
const motionState = state(ID.motion);
const occupied = presenceState === 'on' || motionState === 'on';
const confidentlyAbsent = presenceState === 'off' && motionState === 'off';
const sunState = state(ID.sun);

const actions = [];
const trace = [
  {
    id: 'presence_sensor',
    title: 'Датчик присутствия тамбур 2',
    status: presenceState === 'on' ? 'passed' : presenceState === 'off' ? 'failed' : 'skipped',
    actual: presenceState,
    expected: 'on',
    reason: presenceState === null ? 'Нет достоверного состояния.' : null,
  },
  {
    id: 'motion_sensor',
    title: 'Датчик движения тамбур',
    status: motionState === 'on' ? 'passed' : motionState === 'off' ? 'failed' : 'skipped',
    actual: motionState,
    expected: 'on',
    reason: motionState === null ? 'Нет достоверного состояния.' : null,
  },
];

let branch = 'presence_uncertain';
let brightness = null;
let kelvin = null;

if (confidentlyAbsent) {
  branch = 'off_after_10m';
  const chandelierOn = state(ID.chandelier) === 'on';
  const mirrorOn = state(ID.mirror) === 'on';
  if (chandelierOn || mirrorOn) {
    actions.push({id: 'absence_wait', type: 'delay', delaySeconds: 600});
    if (chandelierOn) {
      actions.push(switchAction('chandelier_off', ID.chandelier, 'Люстра тамбур', false));
    }
    if (mirrorOn) {
      actions.push(switchAction('mirror_off', ID.mirror, 'Подсветка зеркала тамбура', false));
    }
  }
} else if (occupied && (hour >= 23 || hour < 9)) {
  branch = 'night_mirror';
  if (state(ID.chandelier) !== 'off') {
    actions.push(switchAction('chandelier_off', ID.chandelier, 'Люстра тамбур', false));
  }
  if (state(ID.mirror) !== 'on') {
    actions.push(switchAction('mirror_on', ID.mirror, 'Подсветка зеркала тамбура', true));
  }
} else if (occupied && hour >= 9 && hour < 23 && sunState === 'above_horizon') {
  branch = 'morning_day';
  const elapsed = Math.max(0, clockMinutes - 9 * 60);
  const step = Math.min(6, Math.floor(elapsed / 30));
  const profile = [
    [5, 6500],
    [15, 6000],
    [30, 5200],
    [45, 4400],
    [60, 3600],
    [75, 2800],
    [85, 2200],
  ][step];
  [brightness, kelvin] = profile;
} else if (occupied && hour >= 9 && hour < 23 && sunState === 'below_horizon') {
  branch = 'sunset_fade';
  const nextSettingRaw = item(ID.sun).attributes && item(ID.sun).attributes.next_setting;
  const nextSetting = Date.parse(String(nextSettingRaw || ''));
  let previousSetting = Number.isFinite(nextSetting) ? nextSetting : timestampMs;
  while (previousSetting > timestampMs) previousSetting -= 24 * 60 * 60 * 1000;
  const elapsed = Math.max(0, Math.floor((timestampMs - previousSetting) / 60000));
  const step = Math.min(5, Math.floor(elapsed / 30));
  const profile = [
    [85, 2200],
    [75, 2800],
    [60, 3600],
    [45, 4400],
    [30, 5200],
    [10, 6500],
  ][step];
  [brightness, kelvin] = profile;
}

if (brightness !== null && kelvin !== null) {
  if (state(ID.mirror) !== 'off') {
    actions.push(switchAction('mirror_off', ID.mirror, 'Подсветка зеркала тамбура', false));
  }
  actions.push(lightAction('brightness', 'set_brightness_percent', brightness));
  const prime = kelvin >= 6500 ? 6400 : Math.min(6500, kelvin + 100);
  actions.push(lightAction('temperature_prime', 'set_color_temperature', prime));
  actions.push({id: 'temperature_wait_1', type: 'delay', delaySeconds: 1});
  actions.push(lightAction('temperature_target', 'set_color_temperature', kelvin));
  actions.push({id: 'temperature_wait_2', type: 'delay', delaySeconds: 1});
  actions.push(lightAction('temperature_confirm', 'set_color_temperature', kelvin));
}

trace.push(
  {
    id: 'absence_policy',
    title: 'Уверенное отсутствие 10 минут',
    status: confidentlyAbsent ? 'selected' : occupied ? 'skipped' : 'failed',
    actual: confidentlyAbsent,
    expected: true,
    reason: !occupied && !confidentlyAbsent
      ? 'Хотя бы один датчик недоступен, выключение запрещено.'
      : null,
  },
  {
    id: 'light_profile',
    title: 'Профиль света тамбура',
    status: branch === 'presence_uncertain' ? 'skipped' : 'selected',
    actual: `${clock}; ${sunState || 'sun unavailable'}`,
    expected: brightness === null ? branch : `${branch}: ${brightness}%, ${kelvin} K`,
    reason: branch === 'presence_uncertain' ? 'Недостаточно достоверных данных.' : null,
  },
);

msg.statusCode = 200;
msg.headers = {
  'content-type': 'application/json; charset=utf-8',
  'cache-control': 'no-store',
};
msg.payload = {
  contract: {name: 'hausman-node-red-scenario-execution', version: 1},
  correlationId: String(request.correlationId || ''),
  scenarioId: 'system-tambur-adaptive-controller',
  status: actions.length ? 'completed' : 'skipped',
  summary: actions.length
    ? `Тамбур: выбран профиль ${branch}.`
    : 'Тамбур: команды не требуются.',
  selectedBranch: branch,
  durationMs: Math.max(0, Date.now() - started),
  trace,
  actions,
};
return msg;
