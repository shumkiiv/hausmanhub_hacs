// HAUSMAN_MANAGED_SCENARIO system-shower-comfort-controller
// The server owns timers and automation ownership. This function can only echo one exact durable action.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const context = r.context && typeof r.context === 'object' ? r.context : {};
const trigger = context.trigger && typeof context.trigger === 'object' ? context.trigger : {};
const controls = context.controls && typeof context.controls === 'object' ? context.controls : {};
const state = controls.state && typeof controls.state === 'object' ? controls.state : {};
const topology = state.topology && typeof state.topology === 'object' ? state.topology : {};
const roles = topology.roles && typeof topology.roles === 'object' ? topology.roles : {};
const allowed = new Set([roles.main, roles.extra, roles.cabinet, roles.fan].filter(v => typeof v === 'string' && v));
const server = state.action && typeof state.action === 'object' ? state.action : null;
const validServer = trigger.source === 'scenario_control' && state.ready === true && server &&
  Object.keys(server).sort().join(',') === 'actionId,targetId,value' && allowed.has(server.targetId) &&
  ['turn_on', 'turn_off'].includes(server.actionId) && server.value === null;
const manualCabinet = trigger.source === 'manual' && trigger.binding === 'shower-cabinet' &&
  trigger.typed_intent === 'toggle' && ['toggle_b2_down', 'on_b2_down'].includes(trigger.trigger_id) &&
  ['on', 'off'].includes(trigger.direct_user_intent);
let action = validServer ? {
  id: 'server_action', type: 'device_action', targetId: server.targetId,
  targetName: 'Управляемое устройство', actionId: server.actionId, actionTitle: 'Серверное действие',
} : manualCabinet ? {
  id: `set_cabinet_${trigger.direct_user_intent}`, type: 'device_action',
  targetId: roles.cabinet, targetName: 'Душевая: подсветка шкафа',
  actionId: `turn_${trigger.direct_user_intent}`,
  actionTitle: trigger.direct_user_intent === 'on' ? 'Включить' : 'Выключить',
} : null;
if (action && (typeof action.targetId !== 'string' || !action.targetId)) action = null;
msg.statusCode = 200;
msg.payload = {
  contract: {name: 'hausman-node-red-scenario-execution', version: 1},
  correlationId: String(r.correlationId || ''), scenarioId: 'system-shower-comfort-controller',
  status: action ? 'completed' : 'skipped',
  selectedBranch: validServer ? 'server_action' : manualCabinet ? 'cabinet_toggle' : 'server_hold',
  actions: action ? [action] : [],
  trace: [{id: 'server_decision', title: 'Решение сервера для душевой', status: action ? 'selected' : 'skipped', actual: state.transition || null, expected: 'ready action', reason: action ? null : 'server_hold'}],
};
return msg;
