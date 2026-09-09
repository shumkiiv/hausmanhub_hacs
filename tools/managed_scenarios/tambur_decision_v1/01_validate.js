// HAUSMAN_TAMBUR_DECISION_STAGE validate_v1
const q = msg.payload;
const invalid = code => { throw new Error(`invalid_decision_input:${code}`); };
const object = (value, code) => value && typeof value === 'object' && !Array.isArray(value)
  ? value : invalid(code);
object(q, 'payload');
const clock = object(q.clock, 'clock');
const bindings = object(q.bindings, 'bindings');
const settings = object(q.settings, 'settings');
object(q.observations, 'observations');
object(q.authority, 'authority');
const durable = object(q.durable, 'durable');
if (!q.contract || q.contract.name !== 'hausman-node-red-decision-input' ||
    q.contract.version !== 1 || q.scenarioId !== 'system-tambur-adaptive-controller' ||
    q.controllerVersion !== 1) invalid('contract');
if (!Number.isSafeInteger(clock.nowMs) || !Number.isInteger(clock.minutesOfDay) ||
    clock.minutesOfDay < 0 || clock.minutesOfDay > 1439 ||
    typeof clock.timezone !== 'string' || !clock.timezone ||
    !Number.isSafeInteger(q.issuedAtMs) || !Number.isSafeInteger(q.expiresAtMs) ||
    q.issuedAtMs > clock.nowMs || clock.nowMs > q.expiresAtMs) invalid('clock');
if (!Array.isArray(bindings.presenceSensors) || bindings.presenceSensors.length < 1 ||
    bindings.presenceSensors.length > 3 || new Set(bindings.presenceSensors).size !== bindings.presenceSensors.length)
  invalid('presence_sensors');
const targets = [bindings.chandelier, bindings.points, bindings.mirror];
if (targets.some(id => typeof id !== 'string' || !id) || new Set(targets).size !== 3)
  invalid('light_bindings');
const at = value => {
  const match = /^(\d\d):(\d\d)$/.exec(String(value));
  if (!match) invalid('setting_time');
  const result = Number(match[1]) * 60 + Number(match[2]);
  if (result > 1439) invalid('setting_time');
  return result;
};
const nextState = {
  phase: durable.phase,
  phaseStartedAtMs: durable.phaseStartedAtMs,
  absenceSinceMs: durable.absenceSinceMs,
  absenceEpoch: durable.absenceEpoch,
  fadeStartPercent: durable.fadeStartPercent,
  fadeStartedAtMs: durable.fadeStartedAtMs,
  fadeReason: durable.fadeReason,
};
msg._hausman = {
  q,
  now: clock.nowMs,
  minute: clock.minutesOfDay,
  times: {
    morning: at(settings.morningStart),
    morningEnd: at(settings.morningEnd),
    eveningLatest: at(settings.eveningLatestStart),
    mainOff: at(settings.mainOff),
    mirrorOff: at(settings.mirrorOff),
  },
  targets: {chandelier: bindings.chandelier, points: bindings.points, mirror: bindings.mirror},
  nextState,
  wakeups: [],
  action: null,
  reason: 'no_action_required',
  trace: [{id: 'validation', title: 'Входной снимок проверен', status: 'passed'}],
};
return msg;
