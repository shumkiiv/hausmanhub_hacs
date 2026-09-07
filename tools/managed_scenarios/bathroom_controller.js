// HAUSMAN_MANAGED_SCENARIO system-bathroom-exhaust-controller
// Plan-only controller: Hausman Core validates and dispatches every action.
const request = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1},
  correlationId: String(request.correlationId || ''), scenarioId: 'system-bathroom-exhaust-controller',
  status: 'skipped', selectedBranch: 'safe_default', actions: [], trace: []};
return msg;
