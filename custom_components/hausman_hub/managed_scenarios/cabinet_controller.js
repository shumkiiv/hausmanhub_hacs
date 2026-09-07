// HAUSMAN_MANAGED_SCENARIO system-cabinet-light-controller
// Office profile selection and its restart-safe settle sequence live on the server.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const context = r.context && typeof r.context === 'object' ? r.context : {};
const trigger = context.trigger && typeof context.trigger === 'object' ? context.trigger : {};
const controls = context.controls && typeof context.controls === 'object' ? context.controls : {};
const state = controls.state && typeof controls.state === 'object' ? controls.state : {};
const server = state.action && typeof state.action === 'object' ? state.action : null;
const valid = trigger.source === 'scenario_control' && state.ready === true && server &&
  Object.keys(server).sort().join(',') === 'actionId,targetId,value' && server.targetId === 'entity_aeaf7c250c68e8c2' &&
  (server.actionId === 'set_brightness_percent' ? Number.isInteger(server.value) && server.value >= 0 && server.value <= 100 :
    server.actionId === 'set_color_temperature' ? Number.isInteger(server.value) && server.value >= 1500 && server.value <= 6500 : false);
const action = valid ? {id: 'server_action', type: 'device_action', targetId: server.targetId, targetName: 'Управляемое устройство', actionId: server.actionId, actionTitle: 'Серверное действие', value: server.value} : null;
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(r.correlationId || ''), scenarioId: 'system-cabinet-light-controller', status: action ? 'completed' : 'skipped', selectedBranch: action ? 'server_action' : 'server_hold', actions: action ? [action] : [], trace: [{id: 'server_decision', title: 'Решение сервера для кабинета', status: action ? 'selected' : 'skipped', actual: state.transition || null, expected: 'ready profile action', reason: action ? null : 'server_hold'}]};
return msg;
