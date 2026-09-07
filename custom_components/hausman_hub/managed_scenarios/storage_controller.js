// HAUSMAN_MANAGED_SCENARIO system-storage-light-controller
// Motion OR presence; absence timer is owned by the server coordinator.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const i = r.inputs && typeof r.inputs === 'object' ? r.inputs : {};
const s = id => i[id] && i[id].state != null ? String(i[id].state) : null;
const light = 'entity_0ec37ef18b4b39a6';
const sensors = Object.keys(i).filter(id => /storage|kladov|motion|presence|occupancy/i.test(id));
const occupied = sensors.some(id => ['on', 'true', 'occupied', 'detected'].includes(s(id)));
const actions = occupied && s(light) !== 'on' ? [{id: 'storage_light_on', type: 'device_action', targetId: light, targetName: 'Кладовка: свет', actionId: 'turn_on', actionTitle: 'Включить'}] : [];
const triggerId = String(r.context && r.context.trigger && r.context.trigger.trigger_id || '');
if (triggerId === 'storage_exhaust_11' || triggerId === 'storage_exhaust_20') actions.push({id: 'storage_exhaust_on', type: 'device_action', targetId: r.bindings && r.bindings.exhaust, targetName: 'Кладовка: вытяжка', actionId: 'turn_on', actionTitle: 'Включить'}, {id: 'storage_exhaust_wait', type: 'delay', delaySeconds: 1800});
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(r.correlationId || ''), scenarioId: 'system-storage-light-controller', status: actions.length ? 'completed' : 'skipped', selectedBranch: triggerId || (occupied ? 'occupied' : 'absent'), actions, trace: [{id: 'occupancy_or', status: occupied ? 'passed' : 'failed', expected: 'motion OR presence'}, {id: 'exhaust_schedule', status: ['storage_exhaust_11', 'storage_exhaust_20'].includes(triggerId) ? 'passed' : 'skipped', expected: '11:00/20:00 for 30 minutes'}]};
return msg;
