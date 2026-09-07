// HAUSMAN_MANAGED_SCENARIO system-bathroom-exhaust-controller
// Bathroom lights are evidence only. The fan is the sole physical output.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const context = r.context && typeof r.context === 'object' ? r.context : {};
const trigger = context.trigger && typeof context.trigger === 'object' ? context.trigger : {};
const controls = context.controls && typeof context.controls === 'object' ? context.controls : {};
const state = controls.state && typeof controls.state === 'object' ? controls.state : {};
const server = state.action && typeof state.action === 'object' ? state.action : null;
const valid = trigger.source === 'scenario_control' && state.ready === true && server &&
  Object.keys(server).sort().join(',') === 'actionId,targetId,value' &&
  server.targetId === 'entity_c15f5df5382ee180' && ['turn_on', 'turn_off'].includes(server.actionId) && server.value === null;
const action = valid ? {id: 'server_action', type: 'device_action', targetId: server.targetId, targetName: 'Управляемое устройство', actionId: server.actionId, actionTitle: 'Серверное действие'} : null;
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(r.correlationId || ''), scenarioId: 'system-bathroom-exhaust-controller', status: action ? 'completed' : 'skipped', selectedBranch: action ? 'server_action' : 'server_hold', actions: action ? [action] : [], trace: [{id: 'server_decision', title: 'Решение сервера для ванной', status: action ? 'selected' : 'skipped', actual: state.transition || null, expected: 'ready fan action', reason: action ? null : 'server_hold'}]};
return msg;
