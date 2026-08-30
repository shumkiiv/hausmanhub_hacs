// HAUSMAN_MANAGED_SCENARIO system-shower-comfort-controller
// Душевая: единый выбор света и вытяжки. Физические команды выполняет Hausman.
const started = Date.now();
const request = (msg.payload && typeof msg.payload === 'object') ? msg.payload : {};
const inputs = (request.inputs && typeof request.inputs === 'object') ? request.inputs : {};

const ID = Object.freeze({
  presence: 'entity_d1fb2cbf2a691bba',
  humidity: 'entity_fd3945cf1a2110f8',
  sun: 'entity_6b9ccdab9bb484b2',
  main: 'entity_4be32416634e6416',
  extra: 'entity_1fdcd8b244637246',
  fan: 'entity_afef5df0e0cae309',
  cabinet: 'entity_e7a7c61eec7bdff8',
});

const NAME = Object.freeze({
  main: 'Душевая: основной свет',
  extra: 'Душевая: дополнительный свет',
  fan: 'Душевая: вытяжка',
  cabinet: 'Душевая: подсветка шкафа',
});

function state(targetId) {
  const item = inputs[targetId];
  if (!item || item.state === null || item.state === undefined) return null;
  return String(item.state);
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

function setSwitch(actions, key, turnOn, force = false) {
  const current = state(ID[key]);
  const desired = turnOn ? 'on' : 'off';
  if (!force && current === desired) return;
  actions.push(switchAction(`set_${key}_${desired}`, ID[key], NAME[key], turnOn));
}

const timestampMs = Number(request.context && request.context.timestampMs) || Date.now();
// Home Assistant работает в Asia/Omsk (UTC+06:00, без сезонного перевода).
const local = new Date(timestampMs + 6 * 60 * 60 * 1000);
const hour = local.getUTCHours();
const minute = local.getUTCMinutes();
const clock = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
const presenceState = state(ID.presence);
const occupied = presenceState === 'on';
const presenceKnown = occupied || presenceState === 'off';
const humidityRaw = state(ID.humidity);
const humidity = humidityRaw === null ? Number.NaN : Number(humidityRaw);
const humidityKnown = Number.isFinite(humidity);
const humid = humidityKnown && humidity > 55;
const sunState = state(ID.sun);
const night = hour >= 23 || hour < 9;
const daylight = hour >= 9 && hour < 23 && sunState === 'above_horizon';
const evening = hour >= 9 && hour < 23 && sunState === 'below_horizon';

const actions = [];
const trace = [
  {
    id: 'presence',
    title: 'Присутствие в душевой',
    status: presenceKnown ? (occupied ? 'passed' : 'failed') : 'skipped',
    actual: presenceState,
    expected: 'on',
    reason: presenceKnown ? null : 'Датчик присутствия недоступен, свет не переключается вслепую.',
  },
  {
    id: 'humidity',
    title: 'Влажность выше 55%',
    status: humidityKnown ? (humid ? 'passed' : 'failed') : 'skipped',
    actual: humidityKnown ? humidity : null,
    expected: '>55',
    reason: humidityKnown ? null : 'Нет достоверного показания влажности.',
  },
];

let lightBranch = 'light_unknown';
const delayedLightOffKeys = [];
if (presenceKnown && !occupied) {
  lightBranch = 'light_off_5m';
  for (const key of ['main', 'extra', 'cabinet']) {
    if (state(ID[key]) === 'on') delayedLightOffKeys.push(key);
  }
} else if (occupied && night) {
  lightBranch = 'night_cabinet';
  setSwitch(actions, 'main', false);
  setSwitch(actions, 'extra', false);
  // A forced idempotent turn-on lets Hausman detect a pre-existing manual
  // choice and block the complete interchangeable light profile.
  setSwitch(actions, 'cabinet', true, true);
} else if (occupied && daylight) {
  lightBranch = 'day_main';
  setSwitch(actions, 'main', true, true);
  setSwitch(actions, 'extra', false);
  setSwitch(actions, 'cabinet', false);
} else if (occupied && evening) {
  lightBranch = 'evening_extra';
  setSwitch(actions, 'main', false);
  setSwitch(actions, 'extra', true, true);
  setSwitch(actions, 'cabinet', false);
}

trace.push({
  id: 'light_profile',
  title: 'Профиль света',
  status: lightBranch === 'light_unknown' ? 'skipped' : 'selected',
  actual: `${clock}; ${sunState || 'sun unavailable'}`,
  expected: lightBranch,
  reason: lightBranch === 'light_unknown'
    ? 'Для дневного профиля нужны доступные датчики присутствия и солнца.'
    : null,
});

let fanBranch = 'fan_hold';
const fanState = state(ID.fan);
let delayedFanOff = false;
if (humid) {
  fanBranch = 'fan_humidity';
  setSwitch(actions, 'fan', true);
} else if (occupied && fanState !== 'on') {
  fanBranch = 'fan_presence_2m';
  actions.push({id: 'fan_presence_wait', type: 'delay', delaySeconds: 120});
  actions.push(switchAction('set_fan_on', ID.fan, NAME.fan, true));
} else if (presenceKnown && !occupied && humidityKnown && fanState === 'on') {
  fanBranch = 'fan_off_5m';
  delayedFanOff = true;
}

if (delayedLightOffKeys.length || delayedFanOff) {
  actions.push({id: 'absence_wait', type: 'delay', delaySeconds: 300});
  for (const key of delayedLightOffKeys) {
    actions.push(switchAction(`set_${key}_off`, ID[key], NAME[key], false));
  }
  if (delayedFanOff) {
    actions.push(switchAction('set_fan_off', ID.fan, NAME.fan, false));
  }
}

trace.push({
  id: 'fan_policy',
  title: 'Режим вытяжки',
  status: 'selected',
  actual: fanState,
  expected: fanBranch,
  reason: fanBranch === 'fan_hold' ? 'Текущее состояние вытяжки менять не требуется.' : null,
});

msg.statusCode = 200;
msg.headers = {
  'content-type': 'application/json; charset=utf-8',
  'cache-control': 'no-store',
};
msg.payload = {
  contract: {name: 'hausman-node-red-scenario-execution', version: 1},
  correlationId: String(request.correlationId || ''),
  scenarioId: 'system-shower-comfort-controller',
  status: actions.length ? 'completed' : 'skipped',
  summary: actions.length
    ? `Душевая: выбран профиль ${lightBranch}, вытяжка ${fanBranch}.`
    : 'Душевая: команды не требуются.',
  selectedBranch: `${lightBranch}__${fanBranch}`,
  durationMs: Math.max(0, Date.now() - started),
  trace,
  actions,
};
return msg;
