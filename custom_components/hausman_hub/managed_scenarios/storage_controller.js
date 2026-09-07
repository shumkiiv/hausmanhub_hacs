// HAUSMAN_MANAGED_SCENARIO system-storage-light-controller
// Motion OR presence; absence timer is owned by the server coordinator.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const i = r.inputs && typeof r.inputs === 'object' ? r.inputs : {};
const s = id => i[id] && i[id].state != null ? String(i[id].state) : null;
const light = 'entity_0ec37ef18b4b39a6';
const sensors = ['entity_00dcf0ebdc0bc6cb'];
const occupied = sensors.some(id => ['on', 'true', 'occupied', 'detected'].includes(s(id)));
const actions = occupied && s(light) !== 'on' ? [{id: 'storage_light_on', type: 'device_action', targetId: light, targetName: 'Кладовка: свет', actionId: 'turn_on', actionTitle: 'Включить'}] : [];
const triggerId = String(r.context && r.context.trigger && r.context.trigger.trigger_id || '');
// Exhaust remains disabled until a verified live binding is provisioned.
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(r.correlationId || ''), scenarioId: 'system-storage-light-controller', status: actions.length ? 'completed' : 'skipped', selectedBranch: triggerId || (occupied ? 'occupied' : 'absent'), actions, trace: [{id: 'occupancy_or', status: occupied ? 'passed' : 'failed', expected: 'motion OR presence'}, {id: 'exhaust_schedule', status: ['storage_exhaust_11', 'storage_exhaust_20'].includes(triggerId) ? 'passed' : 'skipped', expected: '11:00/20:00 for 30 minutes'}]};
return msg;
