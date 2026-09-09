// HAUSMAN_TAMBUR_DECISION_STAGE next_step_v1
const t = msg._hausman;
const q = t.q;
const id = t.targets;
const obs = target => q.observations[target];
const auth = target => q.authority[target];
const addWakeup = (ident, kind, dueAtMs) => {
  if (Number.isSafeInteger(dueAtMs) && dueAtMs > t.now && !t.wakeups.some(item => item.id === ident))
    t.wakeups.push({id: ident, kind, dueAtMs});
};
const decide = (reason, target, actionId, value) => {
  const state = obs(target);
  const owner = auth(target);
  if (!state || !owner || !t.fresh[target]) {
    t.reason = 'unsafe_action_snapshot';
    return false;
  }
  const action = {
    id: `${String(q.correlationId).slice(0, 119)}.act`, targetId: target, actionId,
    authorityGeneration: owner.generation, observedRevision: state.revision,
  };
  if (value !== undefined) action.value = value;
  t.action = action;
  t.reason = reason;
  return true;
};
const transition = (phase, reason) => {
  if (t.nextState.phase !== phase) t.nextState.phaseStartedAtMs = t.now;
  t.nextState.phase = phase;
  t.reason = reason;
};
const startOrContinueFade = reason => {
  const chandelier = obs(id.chandelier);
  if (t.automatic[id.chandelier] && chandelier.state === 'on') {
    const continuing = q.durable.fadeReason === reason && q.durable.fadeStartedAtMs !== null;
    const begun = continuing ? q.durable.fadeStartedAtMs : t.now;
    const start = continuing ? q.durable.fadeStartPercent :
      (Number.isInteger(chandelier.brightnessPercent) ? chandelier.brightnessPercent : null);
    t.nextState.phase = 'fade';
    t.nextState.phaseStartedAtMs = begun;
    t.nextState.fadeStartedAtMs = begun;
    t.nextState.fadeStartPercent = start;
    t.nextState.fadeReason = reason;
    const finish = begun + q.settings.fadeSeconds * 1000;
    if (t.now >= finish || start === 0)
      return decide(`${reason}_fade_complete`, id.chandelier, 'turn_off');
    if (start === null || !Number.isInteger(chandelier.brightnessPercent)) {
      addWakeup('tambur.fade', 'fade', finish);
      t.reason = `${reason}_fade_brightness_unknown`;
      return true;
    }
    const ratio = Math.max(0, 1 - (t.now - begun) / (q.settings.fadeSeconds * 1000));
    const level = Math.floor(start * ratio);
    addWakeup('tambur.fade', 'fade', Math.min(finish, t.now + 1000));
    if (level < chandelier.brightnessPercent)
      return decide(`${reason}_fade_step`, id.chandelier, 'set_brightness_percent', level);
    t.reason = continuing ? 'fade_snapshot_already_lower' : `${reason}_fade_started`;
    return true;
  }
  if (t.automatic[id.points] && obs(id.points).state === 'on')
    return decide(`${reason}_points_off`, id.points, 'turn_off');
  transition(
    reason === 'night' ? 'night' : 'idle',
    reason === 'night' ? 'night_handover_complete' : 'absence_fade_complete',
  );
  t.nextState.fadeStartPercent = null;
  t.nextState.fadeStartedAtMs = null;
  t.nextState.fadeReason = null;
  return true;
};

if (!t.mirrorScheduled && t.automatic[id.mirror] && obs(id.mirror).state === 'on') {
  decide('mirror_schedule_off', id.mirror, 'turn_off');
} else if (t.mirrorScheduled) {
  let handover = false;
  let blockedReason = null;
  if (q.durable.pendingReceiptId !== null) {
    if (t.receiptInvalid) {
      blockedReason = 'mirror_handover_failed';
    } else if (t.pendingReceipt === null) {
      blockedReason = 'mirror_receipt_pending';
    } else if (t.pendingReceipt.status !== 'confirmed') {
      blockedReason = 'mirror_handover_failed';
    } else if (t.automatic[id.mirror] && obs(id.mirror).state === 'on') {
      handover = true;
    } else {
      blockedReason = 'mirror_confirmation_incomplete';
    }
  } else if (t.automatic[id.mirror] && obs(id.mirror).state === 'on') {
    handover = true;
  } else if (auth(id.mirror) && auth(id.mirror).owner === 'manual' &&
      t.fresh[id.mirror] && obs(id.mirror).state === 'on') {
    handover = true;
  } else if (t.protected[id.mirror]) {
    blockedReason = 'mirror_handover_protected';
  } else {
    const attempted = q.durable.phase === 'night' && Number.isSafeInteger(q.durable.phaseStartedAtMs) &&
      t.now >= q.durable.phaseStartedAtMs && t.now - q.durable.phaseStartedAtMs < t.mirrorWindowMs;
    if (attempted) {
      blockedReason = 'mirror_handover_not_retried';
    } else if (t.available[id.mirror] && obs(id.mirror).state === 'off') {
      transition('mirror_handover', 'mirror_schedule_on');
      t.nextState.fadeReason = 'night';
      decide('mirror_schedule_on', id.mirror, 'turn_on');
    } else {
      blockedReason = 'mirror_handover_unavailable';
    }
  }
  if (!t.action && handover) startOrContinueFade('night');
  if (!t.action && blockedReason) {
    transition(blockedReason === 'mirror_receipt_pending' ? 'mirror_handover' : 'night', blockedReason);
    if (blockedReason !== 'mirror_receipt_pending') {
      t.nextState.fadeStartPercent = null;
      t.nextState.fadeStartedAtMs = null;
      t.nextState.fadeReason = null;
    }
  }
}

const updateAutomaticProfile = () => {
  if (!t.mainAllowed || t.profilePercent === null) return false;
  const chandelier = obs(id.chandelier);
  if (!chandelier || !t.fresh[id.chandelier] || t.protected[id.chandelier]) return false;
  if ((chandelier.state === 'off' && t.available[id.chandelier] && t.activationRequested) ||
      (chandelier.state === 'on' && t.automatic[id.chandelier] &&
       chandelier.brightnessPercent !== t.profilePercent)) {
    addWakeup('tambur.profile', 'profile', t.profileNextAt);
    return decide('automatic_profile_brightness', id.chandelier, 'set_brightness_percent', t.profilePercent);
  }
  if (chandelier.state === 'on' && t.automatic[id.chandelier] &&
      chandelier.colorTemperatureKelvin !== t.profileKelvin) {
    addWakeup('tambur.profile', 'profile', t.profileNextAt);
    return decide('automatic_profile_temperature', id.chandelier, 'set_color_temperature', t.profileKelvin);
  }
  return false;
};

if (!t.action && t.absenceDue) startOrContinueFade('absence');

const profileRefresh = t.activationRequested || !t.absent || q.event.kind === 'clock' ||
  q.event.kind === 'settings' ||
  (q.event.kind === 'wakeup' && q.event.wakeupId === 'tambur.profile');
if (!t.action && t.mainAllowed && profileRefresh &&
    (t.activationRequested || t.automatic[id.chandelier])) {
  if (t.activationRequested && t.nextState.fadeReason !== 'night') {
    t.nextState.phase = 'occupied';
    t.nextState.phaseStartedAtMs = q.durable.phase === 'occupied' ? q.durable.phaseStartedAtMs : t.now;
    t.nextState.absenceSinceMs = null;
    t.nextState.absenceEpoch = null;
    t.nextState.fadeStartPercent = null;
    t.nextState.fadeStartedAtMs = null;
    t.nextState.fadeReason = null;
    t.wakeups = t.wakeups.filter(item => item.kind !== 'absence' && item.kind !== 'fade');
  }
  updateAutomaticProfile();
  if (!t.action && t.activationRequested && !t.protected[id.points] &&
      t.available[id.points] && obs(id.points).state === 'off')
    decide('automatic_points_on', id.points, 'turn_on');
  if (!t.action && t.activationRequested)
    t.reason = t.protected[id.chandelier] || t.protected[id.points]
      ? 'manual_authority_preserved' : 'presence_already_lit';
}

if (!t.action && t.reason === 'no_action_required') {
  if (!t.mainAllowed) t.reason = 'automatic_on_forbidden';
  else if (t.waitReason) t.reason = t.waitReason;
  else if (Object.values(t.protected).some(Boolean)) t.reason = 'manual_authority_preserved';
}
t.trace.push({
  id: 'next_step', title: 'Следующий шаг выбран', status: t.action ? 'selected' : 'skipped',
  actual: t.reason, expected: 'at_most_one_action', reason: null,
});
return msg;
