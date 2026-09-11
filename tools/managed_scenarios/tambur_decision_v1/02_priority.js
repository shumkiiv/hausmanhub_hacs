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
const manualOffProtected = id => {
  const state = observation(id);
  if (!state || state.state !== 'off' || !t.fresh[id]) return false;
  if (!Number.isSafeInteger(state.observedAtMs) || state.observedAtMs <= 0) return false;
  const receipts = Array.isArray(q.receipts) ? q.receipts : [];
  const owned = receipts.filter(item => item && item.targetId === id);
  if (!owned.length) return false;
  const lastOff = owned
    .filter(item => item.actionId === 'turn_off' && item.status === 'confirmed' &&
      Number.isSafeInteger(item.observedAtMs))
    .sort((left, right) => right.observedAtMs - left.observedAtMs)[0];
  if (lastOff && lastOff.observedAtMs >= state.observedAtMs) return false;
  const absentSince = q.durable && Number.isSafeInteger(q.durable.absenceSinceMs)
    ? q.durable.absenceSinceMs : null;
  // Minimum time plus a confirmed absence after the manual OFF: staying in the
  // room keeps the light under manual control instead of re-lighting it.
  if (absentSince === null || absentSince < state.observedAtMs) return true;
  const minimumMs = Number.isSafeInteger(q.settings.manualOffMinSeconds)
    ? q.settings.manualOffMinSeconds * 1000 : 600000;
  return t.now - state.observedAtMs < minimumMs;
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
t.protected = Object.fromEntries(Object.values(t.targets).map(id => [id, protectedTarget(id) || manualOffProtected(id)]));
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
