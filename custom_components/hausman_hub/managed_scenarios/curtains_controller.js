// HAUSMAN_MANAGED_SCENARIO system-curtains-privacy-controller
// Per-cover latch and sunrise trust come only from the server controls snapshot.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const i = r.inputs && typeof r.inputs === 'object' ? r.inputs : {};
const context = r.context && typeof r.context === 'object' ? r.context : {};
const trigger = context.trigger && typeof context.trigger === 'object' ? context.trigger : {};
const controls = context.controls && typeof context.controls === 'object' ? context.controls : {};
const policy = controls.policy && typeof controls.policy === 'object' ? controls.policy : {};
const state = controls.state && typeof controls.state === 'object' ? controls.state : {};
const protectedTargets = state.targets && typeof state.targets === 'object' ? state.targets : {};
const kitchenCap = Number.isInteger(policy.kitchenCoverCapPercent) && policy.kitchenCoverCapPercent >= 1 && policy.kitchenCoverCapPercent <= 100 ? policy.kitchenCoverCapPercent : null;
const officeCap = Number.isInteger(policy.cabinetCoverCapPercent) && policy.cabinetCoverCapPercent >= 1 && policy.cabinetCoverCapPercent <= 100 ? policy.cabinetCoverCapPercent : null;
const configured = [['entity_8746cfd7f6f7103d', 'Шторы гостиная', 100], ['entity_2da2065add6e2168', 'Шторы кухня', kitchenCap], ['entity_1e0b476b7d082cc0', 'Шторы Алисы', 100], ['entity_9164132c7692d6f5', 'Шторы кабинет', officeCap]];
const manual = trigger.source === 'manual' || (trigger.source === 'nested' && trigger.origin_source === 'manual');
const manualOpen = manual && trigger.trigger_id === 'manual_open_all';
const manualClose = manual && trigger.trigger_id === 'manual_close_all';
const trustedSunrise = state.ready === true && state.trustedSunrise === true && state.transition === 'trusted_sunrise';
const automaticClose = !manual && ['sunset', 'low_lux', 'living_light_on', 'service_light_on'].includes(trigger.trigger_id);
const actions = [];
const trace = [];
for (const [targetId, targetName, cap] of configured) {
  const input = i[targetId];
  const rawPosition = input && input.attributes && input.attributes.current_position;
  const validState = input && ['open', 'opening', 'closed', 'closing'].includes(String(input.state));
  const pos = Number.isInteger(rawPosition) && rawPosition >= 0 && rawPosition <= 100 ? rawPosition : null;
  const protection = protectedTargets[targetId];
  let reason = 'trigger_not_authorized';
  if (!validState || pos === null) reason = 'position_unavailable';
  else if ((manualOpen || trustedSunrise) && cap === null) reason = 'curtain_policy_unavailable';
  else if (manualOpen) {
    actions.push({id: `cover_${targetId.replace(/[^a-z0-9]/gi, '_')}`, type: 'device_action', targetId, targetName, actionId: 'set_position', actionTitle: 'Положение', value: cap});
    reason = pos === cap ? 'manual_open_no_op_intent' : 'manual_open';
  } else if (manualClose) {
    actions.push({id: `cover_${targetId.replace(/[^a-z0-9]/gi, '_')}`, type: 'device_action', targetId, targetName, actionId: 'set_position', actionTitle: 'Положение', value: 0});
    reason = 'manual_close';
  } else if (trustedSunrise && protection && protection.morningOpenAllowed === true) {
    if (pos !== cap) actions.push({id: `cover_${targetId.replace(/[^a-z0-9]/gi, '_')}`, type: 'device_action', targetId, targetName, actionId: 'set_position', actionTitle: 'Положение', value: cap});
    reason = pos === cap ? 'already_open' : 'trusted_sunrise';
  } else if (automaticClose) {
    const allowed = state.ready === true && protection && protection.automaticCloseAllowed === true;
    if (allowed && pos > (targetId === 'entity_9164132c7692d6f5' ? 20 : 5)) actions.push({id: `cover_${targetId.replace(/[^a-z0-9]/gi, '_')}`, type: 'device_action', targetId, targetName, actionId: 'set_position', actionTitle: 'Положение', value: 0});
    reason = !allowed ? 'manual_open_latched' : 'automatic_close';
  }
  trace.push({id: targetId, title: targetName, status: actions.some(action => action.targetId === targetId) ? 'selected' : 'skipped', expected: `cap ${cap}%`, reason});
}
const branch = manualOpen ? 'manual_open_all' : manualClose ? 'manual_close_all' : trustedSunrise ? 'trusted_sunrise' : automaticClose ? 'automatic_close' : 'safe_default';
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(r.correlationId || ''), scenarioId: 'system-curtains-privacy-controller', status: actions.length ? 'completed' : 'skipped', selectedBranch: branch, actions, trace};
return msg;
