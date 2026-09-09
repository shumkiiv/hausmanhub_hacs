// HAUSMAN_TAMBUR_DECISION_STAGE priority_v1
const t = msg._hausman;
const q = t.q;
const observation = id => q.observations[id];
const authority = id => q.authority[id];
const fresh = id => {
  const item = observation(id);
  return !!item && item.fresh === true && item.continuityEpoch === q.observationEpoch &&
    (item.state === 'on' || item.state === 'off');
};
const protectedTarget = id => {
  const item = authority(id);
  return !item || item.protectionActive === true || item.owner === 'manual' || item.owner === 'uncertain';
};
const automatic = id => {
  const owner = authority(id);
  const state = observation(id);
  return fresh(id) && !!owner && owner.owner === 'automatic' && owner.protectionActive === false &&
    typeof owner.confirmedReceiptId === 'string' &&
    owner.confirmedStateRevision === state.revision;
};
const available = id => {
  const owner = authority(id);
  return fresh(id) && !!owner && owner.protectionActive === false &&
    (owner.owner === 'none' || automatic(id));
};
const sensorFresh = q.bindings.presenceSensors.map(fresh);
t.present = q.bindings.presenceSensors.some(id => fresh(id) && observation(id).state === 'on');
t.absent = sensorFresh.every(Boolean) && q.bindings.presenceSensors.every(id => observation(id).state === 'off');
t.sensorReliable = sensorFresh.every(Boolean);
t.confirmedArrival = q.event.kind === 'arrival' ||
  (q.event.kind === 'sensor' && q.bindings.presenceSensors.includes(q.event.targetId) &&
   fresh(q.event.targetId) && observation(q.event.targetId).state === 'on');
t.activationRequested = t.present || q.event.kind === 'arrival';
t.fresh = Object.fromEntries(Object.values(t.targets).map(id => [id, fresh(id)]));
t.protected = Object.fromEntries(Object.values(t.targets).map(id => [id, protectedTarget(id)]));
t.automatic = Object.fromEntries(Object.values(t.targets).map(id => [id, automatic(id)]));
t.available = Object.fromEntries(Object.values(t.targets).map(id => [id, available(id)]));
t.pendingReceipt = q.durable.pendingReceiptId === null ? null :
  q.receipts.find(item => item.id === q.durable.pendingReceiptId) || null;
t.receiptInvalid = !!t.pendingReceipt &&
  (t.pendingReceipt.targetId !== t.targets.mirror || t.pendingReceipt.actionId !== 'turn_on');
t.trace.push({
  id: 'priority', title: 'Ручной приоритет и присутствие', status: t.sensorReliable ? 'passed' : 'failed',
  actual: t.present ? 'present' : t.absent ? 'absent' : 'unreliable', expected: 'fresh_sensor_evidence',
  reason: t.sensorReliable ? null : 'sensor_continuity_interrupted',
});
return msg;
