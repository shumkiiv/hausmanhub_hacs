// HAUSMAN_TAMBUR_DECISION_STAGE waits_v1
const t = msg._hausman;
const q = t.q;
const addWakeup = (id, kind, dueAtMs) => {
  if (Number.isSafeInteger(dueAtMs) && dueAtMs > t.now && !t.wakeups.some(item => item.id === id))
    t.wakeups.push({id, kind, dueAtMs});
};
addWakeup('tambur.mirror', 'mirror', t.mirrorNextAt);
if (t.profileNextAt !== null && t.automatic[t.targets.chandelier])
  addWakeup('tambur.profile', 'profile', t.profileNextAt);
const holdDeadlines = Object.values(q.authority).flatMap(item =>
  [item.protectedUntilMs, item.manualOnHoldUntilMs].filter(value => Number.isSafeInteger(value) && value > t.now));
if (holdDeadlines.length) addWakeup('tambur.hold', 'hold', Math.min(...holdDeadlines));

const activeNightFade = q.durable.fadeReason === 'night' &&
  Number.isSafeInteger(q.durable.fadeStartedAtMs);
t.nightTransitionHandled = q.durable.phase === 'night' && q.durable.fadeReason === null &&
  Number.isSafeInteger(q.durable.phaseStartedAtMs) && t.mirrorScheduled &&
  t.now >= q.durable.phaseStartedAtMs && t.now - q.durable.phaseStartedAtMs < t.mirrorWindowMs;
t.nightFadeCancelled = activeNightFade && t.confirmedArrival;

if (t.nightFadeCancelled) {
  t.nextState = {
    phase: 'night', phaseStartedAtMs: t.now, absenceSinceMs: null,
    absenceEpoch: null, fadeStartPercent: null, fadeStartedAtMs: null, fadeReason: null,
  };
  t.wakeups = t.wakeups.filter(item => item.kind !== 'absence' && item.kind !== 'fade');
  t.nightTransitionHandled = true;
  t.waitReason = 'night_arrival_cancelled';
  t.reason = 'night_arrival_cancelled';
} else if (t.present && t.nextState.fadeReason !== 'night' && !t.nightTransitionHandled) {
  t.nextState = {
    phase: 'occupied',
    phaseStartedAtMs: t.nextState.phase === 'occupied' ? t.nextState.phaseStartedAtMs : t.now,
    absenceSinceMs: null, absenceEpoch: null, fadeStartPercent: null,
    fadeStartedAtMs: null, fadeReason: null,
  };
  t.waitReason = 'presence_confirmed';
} else if (!t.sensorReliable) {
  if (t.nextState.fadeReason !== 'night' && !t.nightTransitionHandled) {
    t.nextState = {
      phase: 'idle', phaseStartedAtMs: t.now, absenceSinceMs: null,
      absenceEpoch: null, fadeStartPercent: null, fadeStartedAtMs: null, fadeReason: null,
    };
  } else {
    t.nextState.absenceSinceMs = null;
    t.nextState.absenceEpoch = null;
  }
  t.waitReason = 'absence_continuity_reset';
} else if (t.absent) {
  const preserveNightMarker = t.nightTransitionHandled && t.nextState.fadeReason === null;
  const seconds = t.nightMain ? q.settings.absenceNightSeconds : q.settings.absenceDaySeconds;
  const oldEpoch = q.durable.absenceEpoch;
  const restart = q.event.kind === 'recovery' || oldEpoch !== q.observationEpoch;
  if (q.durable.absenceSinceMs === null || restart) {
    if (!preserveNightMarker) {
      t.nextState.phase = 'absent';
      t.nextState.phaseStartedAtMs = t.now;
    }
    t.nextState.absenceSinceMs = t.now;
    t.nextState.absenceEpoch = q.observationEpoch;
    if (t.nextState.fadeReason !== 'night') {
      t.nextState.fadeStartPercent = null;
      t.nextState.fadeStartedAtMs = null;
      t.nextState.fadeReason = null;
    }
    t.waitReason = restart && q.event.kind === 'recovery'
      ? 'recovery_absence_restarted' : 'absence_waiting';
  } else {
    t.nextState.absenceSinceMs = q.durable.absenceSinceMs;
    t.nextState.absenceEpoch = q.observationEpoch;
    t.waitReason = 'absence_waiting';
  }
  const due = t.nextState.absenceSinceMs + seconds * 1000;
  t.absenceDue = t.now >= due;
  if (!t.absenceDue) addWakeup('tambur.absence', 'absence', due);
}
t.trace.push({
  id: 'waits', title: 'Сроки и непрерывность обновлены',
  status: t.sensorReliable ? 'passed' : 'failed', actual: t.waitReason || 'unchanged',
  expected: t.absent ? (t.nightMain ? q.settings.absenceNightSeconds : q.settings.absenceDaySeconds) : null,
  reason: t.sensorReliable ? null : 'unreliable_sensor_resets_absence',
});
return msg;
