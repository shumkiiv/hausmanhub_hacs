// HAUSMAN_MANAGED_SCENARIO system-small-corridor-light-controller
// The server coordinator owns lux hold, policy, generation and every automatic step.
const request = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const controls = request.context && request.context.controls && typeof request.context.controls === 'object'
  ? request.context.controls : {};
const controlState = controls.state && typeof controls.state === 'object' ? controls.state : {};
const ID = Object.freeze({
  relay: 'entity_4be32416634e6416',
  chandelier: 'entity_9ed909332fdaa8fd',
});
const NAME = Object.freeze({
  [ID.relay]: 'Выключатель малого коридора',
  [ID.chandelier]: 'Люстра малого коридора',
});
const deviceAction = (targetId, actionId, value) => {
  const action = {id: 'server_action', type: 'device_action', targetId, targetName: NAME[targetId], actionId};
  if (value !== null && value !== undefined) action.value = value;
  return action;
};
const branch = controlState.ready === true
  ? String(controlState.transition || 'controller_idle')
  : 'stale_generation';
const actions = [];
const action = controlState.action;
if (controlState.ready === true && action && typeof action === 'object' &&
    [ID.relay, ID.chandelier].includes(action.targetId) &&
    ['turn_on', 'turn_off', 'set_brightness_percent', 'set_color_temperature'].includes(action.actionId)) {
  actions.push(deviceAction(action.targetId, action.actionId, action.value));
}
const evidence = controlState.evidence && typeof controlState.evidence === 'object'
  ? controlState.evidence : {};
msg.statusCode = 200;
msg.headers = {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'};
msg.payload = {
  contract: {name: 'hausman-node-red-scenario-execution', version: 1},
  correlationId: String(request.correlationId || ''),
  scenarioId: 'system-small-corridor-light-controller',
  status: actions.length ? 'completed' : 'skipped',
  summary: actions.length ? `Малый коридор: серверная команда ${branch}.` : `Малый коридор: ветка ${branch} не требует команды.`,
  selectedBranch: branch,
  durationMs: 0,
  trace: [
    {id: 'server_generation', title: 'Поколение серверного решения',
      status: controlState.ready === true ? 'passed' : 'failed',
      actual: Number.isInteger(controlState.generation) ? controlState.generation : null,
      expected: Number.isInteger(controls.policyRevision) ? controls.policyRevision : null,
      reason: controlState.ready === true ? null : 'stale_generation'},
    {id: 'server_evidence', title: 'Серверные данные движения',
      status: evidence.motion === 'on' ? 'passed' : 'skipped',
      actual: evidence.motion || 'unknown', expected: branch, reason: null},
  ],
  actions,
};
return msg;
