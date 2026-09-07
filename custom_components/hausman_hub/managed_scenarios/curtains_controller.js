// HAUSMAN_MANAGED_SCENARIO system-curtains-privacy-controller
// Caps are typed plans; only explicit trusted bindings may add extra covers.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const i = r.inputs && typeof r.inputs === 'object' ? r.inputs : {};
const s = id => i[id] && i[id].state != null ? String(i[id].state) : null;
const configured = [['cover.shtory_gostinaia', 'Шторы гостиная', 100], ['cover.0xa4c1385a4bcce3d6', 'Шторы кухня', 80]];
const bindings = r.bindings && typeof r.bindings === 'object' ? r.bindings : {};
for (const key of ['office', 'alice']) if (typeof bindings[key] === 'string' && bindings[key]) configured.push([bindings[key], `Шторы ${key}`, key === 'office' ? 90 : 100]);
const trigger = r.context && r.context.trigger && r.context.trigger.trigger_id;
const manual = r.context && r.context.trigger && r.context.trigger.source === 'manual';
const opening = trigger === 'sunrise' || trigger === 'manual_open_all';
const actions = [];
for (const [targetId, targetName, cap] of configured) {
  const pos = Number(i[targetId] && i[targetId].attributes && i[targetId].attributes.current_position);
  if (!Number.isFinite(pos)) continue;
  const target = opening ? cap : 0;
  if (manual || (trigger === 'sunset' && pos > 5)) actions.push({id: `cover_${targetId.replace(/[^a-z0-9]/gi, '_')}`, type: 'device_action', targetId, targetName, actionId: 'set_position', actionTitle: 'Положение', value: manual ? Math.min(cap, pos) : target});
}
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(r.correlationId || ''), scenarioId: 'system-curtains-privacy-controller', status: actions.length ? 'completed' : 'skipped', selectedBranch: opening ? 'sunrise' : trigger === 'sunset' ? 'sunset' : 'safe_default', actions, trace: configured.map(([targetId, targetName, cap]) => ({id: targetId, title: targetName, status: 'selected', expected: `cap ${cap}%`}))};
return msg;
