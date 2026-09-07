// HAUSMAN_MANAGED_SCENARIO system-cabinet-light-controller
// Plan-only controller: calibration and physical cover movement stay outside it.
const request = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1},
  correlationId: String(request.correlationId || ''), scenarioId: 'system-cabinet-light-controller',
  status: 'skipped', selectedBranch: 'safe_default', actions: [], trace: []};
return msg;
