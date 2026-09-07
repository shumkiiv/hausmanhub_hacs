// HAUSMAN_MANAGED_SCENARIO system-cabinet-light-controller
// Office light uses explicit trusted IDs from the system light catalog.
const r = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const i = r.inputs && typeof r.inputs === 'object' ? r.inputs : {};
const s = id => i[id] && i[id].state != null ? String(i[id].state) : null;
const bindings = r.bindings && typeof r.bindings === 'object' ? r.bindings : {};
const light = bindings.light === 'entity_0123456789abcdef' ? bindings.light : null;
const sensors = Object.keys(i).filter(id => /cabinet|office|kabinet|motion|presence|occupancy/i.test(id));
const occupied = sensors.some(id => ['on', 'true', 'occupied', 'detected'].includes(s(id)));
const manual = r.context && r.context.trigger && r.context.trigger.source === 'manual';
const actions = light && (manual || occupied) && s(light) !== 'on' ? [{id: 'cabinet_light_on', type: 'device_action', targetId: light, targetName: 'Люстра кабинет', actionId: 'turn_on', actionTitle: 'Включить'}] : [];
if (manual) actions.push({id: 'cabinet_neutral', type: 'device_action', targetId: light, targetName: 'Люстра кабинет', actionId: 'set_color_temperature', actionTitle: 'Нейтральная температура', value: 4000}, {id: 'cabinet_full_brightness', type: 'device_action', targetId: light, targetName: 'Люстра кабинет', actionId: 'set_brightness_percent', actionTitle: '100%', value: 100});
msg.statusCode = 200;
msg.payload = {contract: {name: 'hausman-node-red-scenario-execution', version: 1}, correlationId: String(r.correlationId || ''), scenarioId: 'system-cabinet-light-controller', status: actions.length ? 'completed' : 'skipped', selectedBranch: manual ? 'manual' : occupied ? 'occupied' : 'absent', actions, trace: [{id: 'manual_priority', status: manual ? 'selected' : 'skipped'}, {id: 'occupancy_or', status: occupied ? 'passed' : 'failed', expected: 'motion OR presence'}]};
return msg;
