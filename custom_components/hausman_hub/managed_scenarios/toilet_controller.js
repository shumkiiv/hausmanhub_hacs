// HAUSMAN_MANAGED_SCENARIO system-toilet-comfort-controller
// Typed production plan: motion turns on both toilet lights and fan.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const i = r.inputs && typeof r.inputs === 'object' ? r.inputs : {};
const s = id => i[id] && i[id].state != null ? String(i[id].state) : null;
const targets = [
  ['switch.0xacbac0fffebde2d3_1', 'Туалет: свет 1'],
  ['switch.0xacbac0fffebde2d3_2', 'Туалет: свет 2'],
  ['switch.0x54ef44100019f608', 'Туалет: вытяжка'],
];
const motion = ['binary_sensor.datchik_dvizheniia_tualet_zaniatost', 'binary_sensor.0xa4c13889c39443d5_occupancy']
  .some(id => ['on', 'true', 'occupied', 'detected'].includes(s(id)));
const actions = motion ? targets.filter(([id]) => s(id) !== 'on').map(([targetId, targetName]) => ({
  id: `turn_on_${targetId.slice(-4)}`, type: 'device_action', targetId, targetName,
  actionId: 'turn_on', actionTitle: 'Включить',
})) : [];
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(r.correlationId || ''), scenarioId: 'system-toilet-comfort-controller', status: actions.length ? 'completed' : 'skipped', selectedBranch: motion ? 'occupied' : 'absent', actions, trace: [{id: 'occupancy_or', status: motion ? 'passed' : 'failed', expected: 'motion OR occupancy'}]};
return msg;
