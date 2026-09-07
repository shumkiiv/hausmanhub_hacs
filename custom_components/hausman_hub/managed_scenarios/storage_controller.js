// HAUSMAN_MANAGED_SCENARIO system-storage-light-controller
// The server coordinator owns evidence, generation and deadlines.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const i = r.inputs && typeof r.inputs === 'object' ? r.inputs : {};
const s = id => i[id] && i[id].state != null ? String(i[id].state) : null;
const light = 'entity_0ec37ef18b4b39a6';
const controls = r.context && r.context.controls && typeof r.context.controls === 'object' ? r.context.controls : {};
const policy = controls.policy && typeof controls.policy === 'object' ? controls.policy : {};
const controlState = controls.state && typeof controls.state === 'object' ? controls.state : {};
const ready = controlState.ready === true;
const transition = ready ? String(controlState.transition || 'idle') : 'stale_generation';
const bindings = r.bindings && typeof r.bindings === 'object' ? r.bindings : {};
const exhaust = typeof bindings.storageExhaust === 'string' ? bindings.storageExhaust : null;
const action = (id, targetId, targetName, actionId) => ({id, type: 'device_action', targetId, targetName, actionId, actionTitle: actionId === 'turn_on' ? 'Включить' : 'Выключить'});
const actions = [];
if (transition === 'storage_light_on' && s(light) === 'off') {
  actions.push(action('storage_light_on', light, 'Кладовка: свет', 'turn_on'));
} else if (transition === 'storage_light_off_due' && s(light) === 'on') {
  actions.push(action('storage_light_off', light, 'Кладовка: свет', 'turn_off'));
} else if (transition === 'storage_exhaust_on' && exhaust) {
  actions.push(action('storage_exhaust_on', exhaust, 'Кладовка: вытяжка', 'turn_on'));
} else if (transition === 'storage_exhaust_off' && exhaust) {
  actions.push(action('storage_exhaust_off', exhaust, 'Кладовка: вытяжка', 'turn_off'));
}
const evidence = controlState.evidence && typeof controlState.evidence === 'object' ? controlState.evidence : {};
const exhaustTimes = Array.isArray(policy.storageExhaustTimes) ? policy.storageExhaustTimes.join('/') : '11:00/20:00';
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(r.correlationId || ''), scenarioId: 'system-storage-light-controller', status: actions.length ? 'completed' : 'skipped', summary: actions.length ? `Кладовка: выбрана команда ${transition}.` : `Кладовка: ветка ${transition} не требует команды.`, selectedBranch: transition, durationMs: 0, actions, trace: [
  {id: 'server_generation', title: 'Поколение серверного решения', status: ready ? 'passed' : 'failed', actual: Number.isInteger(controlState.generation) ? controlState.generation : null, expected: Number.isInteger(controls.policyRevision) ? controls.policyRevision : null, reason: ready ? null : 'stale_generation'},
  {id: 'occupancy_or', title: 'Движение или присутствие', status: evidence.motion === 'on' || evidence.presence === 'on' ? 'passed' : evidence.motion === 'off' || evidence.presence === 'off' ? 'failed' : 'skipped', actual: `${evidence.motion || 'not_bound'}/${evidence.presence || 'not_bound'}`, expected: 'motion OR presence', reason: null},
  {id: 'ownership', title: 'Подтверждённое владение светом', status: evidence.ownershipRevision ? 'passed' : 'skipped', actual: evidence.ownershipRevision ? true : false, expected: transition === 'storage_light_off_due', reason: null},
  {id: 'exhaust_schedule', title: 'Расписание вытяжки кладовки', status: transition === 'storage_exhaust_unbound' ? 'skipped' : transition.startsWith('storage_exhaust_') ? 'selected' : 'skipped', actual: exhaustTimes, expected: `${Number(policy.storageExhaustRunSeconds || 1800)}s`, reason: transition === 'storage_exhaust_unbound' ? 'binding_unbound' : null},
]};
return msg;
