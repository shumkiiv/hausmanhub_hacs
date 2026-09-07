// HAUSMAN_MANAGED_SCENARIO system-toilet-comfort-controller
// Typed production plan: motion turns on both toilet lights and fan.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const i = r.inputs && typeof r.inputs === 'object' ? r.inputs : {};
const s = id => i[id] && i[id].state != null ? String(i[id].state) : null;
const targets = [
  ['entity_5d95de599d2b5cec', 'Туалет: основной свет'],
  ['entity_6667b3400bce7970', 'Туалет: дополнительный свет'],
  ['entity_9bbb3b0e8cd98627', 'Туалет: вытяжка'],
];
const motion = ['entity_ce73f88bda2e6812', 'entity_56650c782076ed4d']
  .some(id => ['on', 'true', 'occupied', 'detected'].includes(s(id)));
const actions = motion ? targets.filter(([id]) => s(id) !== 'on').map(([targetId, targetName]) => ({
  id: `turn_on_${targetId.slice(-4)}`, type: 'device_action', targetId, targetName,
  actionId: 'turn_on', actionTitle: 'Включить',
})) : [];
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(r.correlationId || ''), scenarioId: 'system-toilet-comfort-controller', status: actions.length ? 'completed' : 'skipped', selectedBranch: motion ? 'occupied' : 'absent', actions, trace: [{id: 'occupancy_or', status: motion ? 'passed' : 'failed', expected: 'motion OR occupancy'}]};
return msg;
