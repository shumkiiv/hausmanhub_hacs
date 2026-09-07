// HAUSMAN_MANAGED_SCENARIO system-toilet-comfort-controller
// Plan-only controller: Hausman Core validates and dispatches every action.
const request = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const occupied = request.inputs && Object.values(request.inputs).some((v) => v && v.state === 'on');
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1},
  correlationId: String(request.correlationId || ''), scenarioId: 'system-toilet-comfort-controller',
  status: occupied ? 'completed' : 'skipped', selectedBranch: occupied ? 'occupied' : 'absent',
  actions: [], trace: [{id: 'occupancy', status: occupied ? 'passed' : 'skipped'}]};
return msg;
