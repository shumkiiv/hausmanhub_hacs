// HAUSMAN_MANAGED_SCENARIO system-storage-light-controller
// Motion OR presence is interpreted by Hausman Core before dispatch.
const request = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1},
  correlationId: String(request.correlationId || ''), scenarioId: 'system-storage-light-controller',
  status: 'skipped', selectedBranch: 'safe_default', actions: [], trace: []};
return msg;
