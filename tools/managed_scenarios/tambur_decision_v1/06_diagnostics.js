// HAUSMAN_TAMBUR_DECISION_STAGE diagnostics_v1
const t = msg._hausman;
const q = t.q;
msg.statusCode = 200;
msg.headers = {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'};
msg.payload = {
  contract: {name: 'hausman-node-red-decision', version: 1},
  correlationId: q.correlationId,
  scenarioId: q.scenarioId,
  planId: q.correlationId,
  controllerVersion: q.controllerVersion,
  settingsRevision: q.settingsRevision,
  baseRevision: q.durable.revision,
  snapshotRevision: q.snapshotRevision,
  observationEpoch: q.observationEpoch,
  expiresAtMs: q.expiresAtMs,
  status: t.action === null ? 'skipped' : 'decided',
  reasonCode: t.reason,
  trace: t.trace,
  nextState: t.nextState,
  wakeups: t.wakeups.slice(0, 4),
  action: t.action,
};
delete msg._hausman;
return msg;
