// HAUSMAN_MANAGED_SCENARIO system-tambur-adaptive-controller
// The server coordinator owns evidence, policy, generation and every automatic step.
const request = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const trigger = request.context && request.context.trigger && typeof request.context.trigger === 'object'
  ? request.context.trigger : {};
const controls = request.context && request.context.controls && typeof request.context.controls === 'object'
  ? request.context.controls : {};
const controlState = controls.state && typeof controls.state === 'object' ? controls.state : {};
const inputs = request.inputs && typeof request.inputs === 'object' ? request.inputs : {};
const ID = Object.freeze({
  chandelier: 'entity_71859313239a14e4',
  points: 'entity_cd0098e5ff95da46',
  mirror: 'entity_fbdf27871edb89bf',
});
const NAME = Object.freeze({
  [ID.chandelier]: 'Люстра тамбур',
  [ID.points]: 'Точки тамбура',
  [ID.mirror]: 'Подсветка зеркала тамбура',
});
const state = targetId => {
  const item = inputs[targetId];
  return item && item.state != null ? String(item.state) : null;
};
const deviceAction = (id, targetId, actionId, value) => {
  const action = {id, type: 'device_action', targetId, targetName: NAME[targetId], actionId};
  if (value !== null && value !== undefined) action.value = value;
  return action;
};
const typedGroup = trigger.source === 'manual' &&
  trigger.binding === 'tambur-light-group' &&
  ['on', 'off', 'toggle'].includes(String(trigger.typed_intent || '')) &&
  ['on', 'off'].includes(String(trigger.direct_user_intent || ''));
const actions = [];
let branch = controlState.ready === true
  ? String(controlState.transition || 'controller_idle')
  : 'stale_generation';
if (typedGroup) {
  const intent = String(trigger.direct_user_intent);
  branch = `direct_user_group_${String(trigger.typed_intent)}_${intent}`;
  if (intent === 'off') {
    actions.push(deviceAction('chandelier_off', ID.chandelier, 'turn_off'));
    actions.push(deviceAction('points_off', ID.points, 'turn_off'));
  } else {
    if (state(ID.chandelier) === 'off') actions.push(deviceAction('chandelier_on', ID.chandelier, 'turn_on'));
    if (state(ID.points) === 'off') actions.push(deviceAction('points_on', ID.points, 'turn_on'));
  }
} else if (controlState.ready === true) {
  const action = controlState.action;
  if (action && typeof action === 'object' &&
      [ID.chandelier, ID.points, ID.mirror].includes(action.targetId) &&
      ['turn_on', 'turn_off', 'set_brightness_percent', 'set_color_temperature'].includes(action.actionId)) {
    actions.push(deviceAction('server_action', action.targetId, action.actionId, action.value));
  }
}
const evidence = controlState.evidence && typeof controlState.evidence === 'object'
  ? controlState.evidence : {};
msg.statusCode = 200;
msg.headers = {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'};
msg.payload = {
  contract: {name: 'hausman-node-red-scenario-execution', version: 1},
  correlationId: String(request.correlationId || ''),
  scenarioId: 'system-tambur-adaptive-controller',
  status: actions.length ? 'completed' : 'skipped',
  summary: actions.length ? `Тамбур: серверная команда ${branch}.` : `Тамбур: ветка ${branch} не требует команды.`,
  selectedBranch: branch,
  durationMs: 0,
  trace: [
    {id: 'server_generation', title: 'Поколение серверного решения',
      status: controlState.ready === true || typedGroup ? 'passed' : 'failed',
      actual: Number.isInteger(controlState.generation) ? controlState.generation : null,
      expected: Number.isInteger(controls.policyRevision) ? controls.policyRevision : null,
      reason: controlState.ready === true || typedGroup ? null : 'stale_generation'},
    {id: 'server_evidence', title: 'Серверные данные присутствия',
      status: evidence.motion === 'on' || evidence.presence === 'on' ? 'passed' : 'skipped',
      actual: `${evidence.motion || 'unknown'}/${evidence.presence || 'unknown'}`,
      expected: branch, reason: null},
  ],
  actions,
};
return msg;
