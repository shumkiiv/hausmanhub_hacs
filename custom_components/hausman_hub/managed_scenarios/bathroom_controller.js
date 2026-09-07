// HAUSMAN_MANAGED_SCENARIO system-bathroom-exhaust-controller
// Typed production plan: bathroom lights/fan follow OR occupancy evidence.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const i = r.inputs && typeof r.inputs === 'object' ? r.inputs : {};
const s = id => i[id] && i[id].state != null ? String(i[id].state) : null;
const occupied = Object.keys(i).some(id => /bathroom|vanna|occupancy|motion/i.test(id) && ['on', 'true', 'occupied', 'detected'].includes(s(id)));
const bindings = r.bindings && typeof r.bindings === 'object' ? r.bindings : {};
const known = [['switch.0xacbac0fffebe38d0_1', 'Ванная: свет 1'], ['switch.0xacbac0fffebe38d0_2', 'Ванная: свет 2'], ['switch.0x54ef44100019fca5', 'Ванная: вытяжка']];
const actions = occupied ? known.filter(([id]) => s(id) !== 'on').map(([targetId, targetName]) => ({id: `turn_on_${targetId.slice(-4)}`, type: 'device_action', targetId, targetName, actionId: 'turn_on', actionTitle: 'Включить'})) : [];
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(r.correlationId || ''), scenarioId: 'system-bathroom-exhaust-controller', status: actions.length ? 'completed' : 'skipped', selectedBranch: occupied ? 'occupied' : 'absent', actions, trace: [{id: 'occupancy_or', status: occupied ? 'passed' : 'failed', expected: 'motion OR presence', bindingCount: Object.keys(bindings).length}]};
return msg;
