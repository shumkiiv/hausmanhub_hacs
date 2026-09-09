// HAUSMAN_TAMBUR_DECISION_STAGE profile_v1
const t = msg._hausman;
const q = t.q;
const s = q.settings;
const x = t.times;
const between = (minute, start, end) => start < end
  ? minute >= start && minute < end
  : minute >= start || minute < end;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
let sunsetMinute = null;
if (q.clock.sunsetAtMs !== null && Number.isSafeInteger(q.clock.sunsetAtMs)) {
  sunsetMinute = t.minute + Math.floor((q.clock.sunsetAtMs - t.now) / 60000);
  sunsetMinute = ((sunsetMinute % 1440) + 1440) % 1440;
}
const eveningStart = clamp(
  Math.min(sunsetMinute === null ? x.eveningLatest : sunsetMinute, x.eveningLatest),
  x.morningEnd,
  x.mainOff,
);
t.mainAllowed = between(t.minute, x.morning, x.mainOff);
t.mirrorScheduled = between(t.minute, x.mainOff, x.mirrorOff);
t.nightMain = !t.mainAllowed;
t.eveningStart = eveningStart;
t.profilePercent = null;
t.profileKelvin = null;
t.profileNextAt = null;
if (t.mainAllowed) {
  if (t.minute < x.morningEnd) {
    const ratio = (t.minute - x.morning) / (x.morningEnd - x.morning);
    t.profilePercent = Math.round(s.minPercent + (s.maxPercent - s.minPercent) * ratio);
    t.profileKelvin = s.dayKelvin;
    t.profileNextAt = t.now + 60000;
  } else if (t.minute < eveningStart) {
    t.profilePercent = s.maxPercent;
    t.profileKelvin = s.dayKelvin;
    t.profileNextAt = t.now + (eveningStart - t.minute) * 60000;
  } else {
    const span = Math.max(1, x.mainOff - eveningStart);
    const ratio = clamp((t.minute - eveningStart) / span, 0, 1);
    t.profilePercent = Math.round(s.maxPercent - (s.maxPercent - s.minPercent) * ratio);
    t.profileKelvin = Math.round(s.dayKelvin - (s.dayKelvin - s.eveningKelvin) * ratio);
    t.profileNextAt = t.now + 60000;
  }
}
const until = target => {
  let delta = (target - t.minute + 1440) % 1440;
  if (delta === 0) delta = 1440;
  return t.now + delta * 60000;
};
t.mirrorNextAt = until(t.mirrorScheduled ? x.mirrorOff : x.mainOff);
t.mirrorWindowMs = ((x.mirrorOff - x.mainOff + 1440) % 1440 || 1440) * 60000;
t.trace.push({
  id: 'profile', title: 'Суточный профиль рассчитан', status: t.mainAllowed ? 'passed' : 'skipped',
  actual: t.profilePercent, expected: t.mainAllowed ? '5..80' : null,
  reason: t.mainAllowed ? null : 'automatic_main_forbidden',
});
return msg;
