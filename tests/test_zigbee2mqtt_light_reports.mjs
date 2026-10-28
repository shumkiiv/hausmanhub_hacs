import assert from 'node:assert/strict';
import {test} from 'node:test';
import Observer, {physicalOnOff, physicalBrightness, physicalColorTemperature} from '../tools/zigbee2mqtt/hausman_light_reports.mjs';

function color(overrides = {}) {
    return physical({cluster:'lightingColorCtrl',device:{ieeeAddr:'0xa4c138784e5cbcd1'},
        data:{colorTemperature:340},...overrides});
}
test('physical colour only for chandelier, distinct from power and level', () => {
    for (const type of ['attributeReport','readResponse']) {
        const msg=color({type}); const r=physicalColorTemperature(msg,BASE_TIME);
        assert.equal(r.value,340); assert.equal(r.source,'physical_color');
        assert.equal(r.attribute,'lightingColorCtrl.colorTemperature');
        assert.equal(physicalOnOff(msg,BASE_TIME),null);
        assert.equal(physicalBrightness(msg,BASE_TIME),null);
    }
});
test('colour rejects cached, malformed and out-of-scope frames', () => {
    for (const changes of [{type:'commandMoveToColorTemp'}, {type:'publishCached'},
        {data:{colorTemperature:true}}, {data:{colorTemperature:152}},
        {data:{colorTemperature:501}}, {data:{colorTemperature:340.1}},
        {data:Object.create({colorTemperature:340})}, {endpoint:{ID:2}},
        {device:{ieeeAddr:'0x603d61fffe761c63'}}, {meta:{}}]) {
        assert.equal(physicalColorTemperature(color(changes),BASE_TIME),null);
    }
});
test('colour is sequenced and deduplicated independently of level and power',async () => {
    const f=fixture(); await f.observer.start();
    await f.listeners.get('device')(level()); f.advance(1);
    await f.listeners.get('device')(color()); f.advance(1);
    await f.listeners.get('device')(color());
    assert.equal(f.published.length,2); assert.equal(f.boundaries.length,0);
    assert.equal(f.published[1].report.source,'physical_color');
    assert.equal(f.published[1].report.sequence,2);
});

const BASE_TIME = 1791991000000;
function level(overrides = {}) {
    return physical({cluster:'genLevelCtrl', device:{ieeeAddr:'0xa4c138784e5cbcd1'},
        data:{currentLevel:64}, ...overrides});
}
test('physical currentLevel only for chandelier; does not become ONOFF', () => {
    for (const type of ['attributeReport','readResponse']) {
        const r=physicalBrightness(level({type}),BASE_TIME);
        assert.equal(r.value,64); assert.equal(r.source,'physical_brightness');
        assert.equal(r.attribute,'genLevelCtrl.currentLevel');
        assert.equal(physicalOnOff(level({type}),BASE_TIME),null);
    }
});
test('brightness rejects cached commands, null/255, wrong scope and absent raw provenance', () => {
    for (const changes of [
        {type:'commandMoveToLevel'}, {type:'publishCached'}, {data:{currentLevel:true}},
        {data:{currentLevel:255}}, {data:{currentLevel:-1}}, {data:{currentLevel:1.5}},
        {data:Object.create({currentLevel:64})}, {endpoint:{ID:2}},
        {device:{ieeeAddr:'0x603d61fffe761c63'}}, {meta:{}},
    ]) assert.equal(physicalBrightness(level(changes),BASE_TIME),null);
});
test('interleaved clusters with same transaction are separate frames, not conflicts', async () => {
    const f=fixture(); await f.observer.start();
    await f.listeners.get('device')(physical({device:{ieeeAddr:'0xa4c138784e5cbcd1'}}));
    f.advance(1); await f.listeners.get('device')(level());
    assert.equal(f.published.length,2); assert.equal(f.published[1].report.sequence,2);
    assert.equal(f.published[1].report.source,'physical_brightness');
    assert.equal(f.boundaries.length,0);
    f.advance(1000); await f.listeners.get('device')(level());
    assert.equal(f.published.length,2);
});
function physical(overrides = {}) {
    return {
        type: 'attributeReport', cluster: 'genOnOff',
        device: {ieeeAddr: '0x603d61fffe761c63'}, endpoint: {ID: 1},
        data: {onOff: 1},
        meta: {zclTransactionSequenceNumber: 7, rawData: Buffer.from([24, 7, 10, 0, 0, 16, 1])},
        ...overrides,
    };
}
function fixture(publishOverride) {
    const published = [], boundaries = [], listeners = new Map();
    let now = BASE_TIME, session = 0, connected = true;
    const denied = () => { throw new Error('Control/cache/settings access forbidden'); };
    const forbidden = new Proxy({}, {get: denied});
    const mqtt = {
        isConnected: () => connected,
        publish: publishOverride ?? (async (topic, payload, options) => {
            const destination = topic.endsWith('/boundary_v1') ? boundaries : published;
            destination.push({topic, report: JSON.parse(payload), options});
        }),
    };
    const eventBus = {
        onDeviceMessage: (_key, fn) => listeners.set('device', fn),
        onAdapterDisconnected: (_key, fn) => listeners.set('adapterGap', fn),
        onMQTTMessagePublished: (_key, fn) => listeners.set('mqttPublished', fn),
        removeListeners: () => listeners.clear(),
    };
    const observer = new Observer(forbidden, mqtt, forbidden, denied, eventBus,
        denied, denied, denied, forbidden, {},
        {nowMs: () => now, newSession: () => `test-session-${++session}`});
    return {observer, published, boundaries, listeners, advance: ms => {now += ms;},
        disconnect: () => {connected = false;}, connect: () => {connected = true;}};
}

test('physical ON, OFF and readResponse are explicit observations', () => {
    for (const type of ['attributeReport', 'readResponse']) {
        for (const value of [0, 1]) {
            const result = physicalOnOff(physical({type, data: {onOff: value}}), BASE_TIME);
            assert.equal(result.value, value ? 'ON' : 'OFF');
            assert.equal(result.observed_at_ms, BASE_TIME);
            assert.equal(result.attribute, 'genOnOff.onOff');
        }
    }
});
test('channel 1 does not confirm channel 2 or merged cached properties', () => {
    const result = physicalOnOff(physical({data: {onOff: 1, state_2: 'OFF'}}), BASE_TIME);
    assert.equal(result.endpoint, 1);
    assert.equal(Object.hasOwn(result, 'state_2'), false);
    assert.equal(Object.hasOwn(result, 'entity_id'), false);
    assert.equal(Object.hasOwn(result, 'authority'), false);
});
test('only four allowed physical endpoint pairs are accepted', () => {
    for (const [ieeeAddr, ID] of [
        ['0x603d61fffe761c63', 1], ['0x603d61fffe761c63', 2],
        ['0xa4c138ffecbc07b5', 1], ['0xa4c138784e5cbcd1', 1],
    ]) assert.ok(physicalOnOff(physical({device: {ieeeAddr}, endpoint: {ID}}), BASE_TIME));
    for (const [ieeeAddr, ID] of [
        ['0x603d61fffe761c63', 3], ['0xa4c138ffecbc07b5', 2],
        ['0xa4c138784e5cbcd1', 242], ['0x0000000000000000', 1],
    ]) assert.equal(physicalOnOff(physical({device: {ieeeAddr}, endpoint: {ID}}), BASE_TIME), null);
});
for (const type of ['commandOn', 'commandOff', 'publishCached', 'groupOptimistic', 'raw']) {
    test(`reject nonphysical input ${type}`, () => {
        assert.equal(physicalOnOff(physical({type}), BASE_TIME), null);
    });
}
for (const onOff of [true, false, '1', 'ON', 2, null, undefined]) {
    test(`reject nonnumeric onOff ${String(onOff)}`, () => {
        assert.equal(physicalOnOff(physical({data: {onOff}}), BASE_TIME), null);
    });
}
test('reject missing/prototype attribute, wrong cluster or missing raw provenance', () => {
    for (const override of [
        {data: {}}, {data: Object.create({onOff: 1})}, {cluster: 'genLevelCtrl'},
        {meta: {}}, {meta: {rawData: Buffer.from([1]), zclTransactionSequenceNumber: 256}},
        {meta: {rawData: 'cached', zclTransactionSequenceNumber: 1}},
    ]) assert.equal(physicalOnOff(physical(override), BASE_TIME), null);
    assert.equal(physicalOnOff(physical(), NaN), null);
    assert.equal(physicalOnOff(physical(), -1), null);
});
test('same-value new frames advance observations; duplicate does not extend receive time', async () => {
    const f = fixture(); await f.observer.start();
    await f.listeners.get('device')(physical()); f.advance(1000);
    await f.listeners.get('device')(physical());
    assert.equal(f.published.length, 1);
    assert.equal(f.published[0].report.observed_at_ms, BASE_TIME);
    await f.listeners.get('device')(physical({meta: {
        zclTransactionSequenceNumber: 8, rawData: Buffer.from([24, 8, 10, 0, 0, 16, 1]),
    }}));
    assert.equal(f.published.length, 2);
    assert.equal(f.published[1].report.value, 'ON');
    assert.equal(f.published[1].report.observed_at_ms, BASE_TIME + 1000);
    assert.equal(f.published[1].report.sequence, 2);
});
test('published data is nonretained on observation-only topic and input is unchanged', async () => {
    const f = fixture(), message = physical();
    const before = JSON.stringify(message);
    await f.observer.start(); await f.listeners.get('device')(message);
    assert.equal(JSON.stringify(message), before);
    assert.equal(f.published[0].topic, 'hausman/light_reports/raw_v1');
    assert.deepEqual(f.published[0].options, {
        clientOptions: {retain: false, qos: 0}, skipReceive: true, skipLog: true,
    });
});
test('stop blocks captured callbacks; explicit start creates a new nonpersistent session', async () => {
    const f = fixture(); await f.observer.start();
    const callback = f.listeners.get('device'); await callback(physical());
    await f.observer.stop(); await callback(physical());
    assert.equal(f.published.length, 1); assert.equal(f.listeners.size, 0);
    await f.observer.start(); await f.listeners.get('device')(physical());
    assert.notEqual(f.published[0].report.session, f.published[1].report.session);
    assert.equal(f.published[1].report.sequence, 1);
});
test('MQTT disconnect, reconnect notice, adapter gap and backwards clock fail closed', async () => {
    for (const gap of ['disconnect', 'reconnect', 'adapter', 'clock']) {
        const f = fixture(); await f.observer.start();
        const callback = f.listeners.get('device');
        await callback(physical());
        if (gap === 'disconnect') f.disconnect();
        if (gap === 'reconnect') await f.listeners.get('mqttPublished')({topic: 'zigbee2mqtt/bridge/state'});
        if (gap === 'adapter') await f.listeners.get('adapterGap')();
        if (gap === 'clock') f.advance(-1000);
        await callback(physical());
        assert.equal(f.observer.running, false, gap);
        assert.equal(f.published.length, 1, gap);
        assert.equal(f.observer.recentFrames.size, 0, gap);
    }
});
test('repeated start does not register additional listeners', async () => {
    const f = fixture(); await f.observer.start(); await f.observer.start();
    assert.equal(f.listeners.size, 3); await f.listeners.get('device')(physical());
    assert.equal(f.published.length, 1);
});

test('same transaction with conflicting payload closes the session instead of confirming two values', async () => {
    const f = fixture(); await f.observer.start();
    const callback = f.listeners.get('device');
    await callback(physical()); f.advance(100);
    await callback(physical({data: {onOff: 0}, meta: {
        zclTransactionSequenceNumber: 7, rawData: Buffer.from([24, 7, 10, 0, 0, 16, 0]),
    }}));
    assert.equal(f.published.length, 1);
    assert.equal(f.observer.running, false);
    assert.equal(f.observer.session, null);
});
test('publish rejection closes the session and subsequent frames cannot continue it', async () => {
    let calls = 0;
    const f = fixture(async () => {calls++; throw new Error('mock transport failure');});
    await f.observer.start(); const callback = f.listeners.get('device');
    await callback(physical()); await callback(physical());
    assert.equal(calls, 2); // One report plus one bounded close notification.
    assert.equal(f.observer.running, false);
    assert.equal(f.observer.inFlight, false);
    assert.equal(f.observer.recentFrames.size, 0);
});
test('128 concurrent frames cannot create more than one pending publication', async () => {
    let release, calls = 0;
    const pending = new Promise(resolve => {release = resolve;});
    const f = fixture(async () => {calls++; await pending;});
    await f.observer.start(); const callback = f.listeners.get('device');
    const first = callback(physical());
    for (let index = 8; index < 136; index++) {
        await callback(physical({meta: {zclTransactionSequenceNumber: index,
            rawData: Buffer.from([24, index, 10, 0, 0, 16, 1])}}));
    }
    assert.equal(calls, 1);
    assert.equal(f.observer.running, false);
    assert.equal(f.observer.recentFrames.size, 0);
    await assert.rejects(f.observer.start(), /has not settled/);
    release(); await first;
    assert.equal(f.observer.inFlight, false);
    await f.observer.start(); assert.equal(f.observer.running, true);
});
test('stop during pending publish prevents restart until that operation settles', async () => {
    let release;
    const pending = new Promise(resolve => {release = resolve;});
    const f = fixture(async () => {await pending;}); await f.observer.start();
    const first = f.listeners.get('device')(physical()); await f.observer.stop();
    await assert.rejects(f.observer.start(), /has not settled/);
    release(); await first;
    assert.equal(f.observer.running, false);
    await f.observer.start(); assert.equal(f.observer.sequence, 0);
});

test('close marker identifies the old session without publishing an entity state', async () => {
    const f = fixture(); await f.observer.start();
    await f.listeners.get('device')(physical());
    const session = f.published[0].report.session;
    await f.observer.stop(); await f.observer.stop();
    assert.equal(f.boundaries.length, 1);
    assert.equal(f.boundaries[0].topic, 'hausman/light_reports/boundary_v1');
    assert.deepEqual(f.boundaries[0].report, {
        schema: 1, source: 'observation_closed', session,
        sequence: 2, observed_at_ms: BASE_TIME,
    });
    assert.equal(f.boundaries[0].options.clientOptions.retain, false);
});

test('late old publication is followed by its close marker before restart', async () => {
    let release, active = 0, peak = 0;
    const output = [];
    const pending = new Promise(resolve => {release = resolve;});
    const f = fixture(async (topic, payload) => {
        active++; peak = Math.max(peak, active);
        if (topic.endsWith('/raw_v1')) await pending;
        output.push({topic, body: JSON.parse(payload)}); active--;
    });
    await f.observer.start();
    const first = f.listeners.get('device')(physical());
    await f.observer.stop();
    await assert.rejects(f.observer.start(), /has not settled/);
    release(); await first;
    assert.equal(peak, 1); assert.equal(output.length, 2);
    assert.equal(output[1].body.source, 'observation_closed');
    assert.equal(output[0].body.session, output[1].body.session);
    await f.observer.start(); assert.notEqual(f.observer.session, output[0].body.session);
});

const bridge = state => ({topic: 'zigbee2mqtt/bridge/state', payload: JSON.stringify({state})});

test('online closes old evidence and starts a fresh session without duplicate listeners', async () => {
    const f = fixture(); await f.observer.start();
    await f.listeners.get('device')(physical());
    const old = f.published[0].report.session;
    f.advance(1);
    await f.listeners.get('mqttPublished')(bridge('online'));
    assert.equal(f.observer.running, true);
    assert.equal(f.listeners.size, 3);
    assert.notEqual(f.observer.session, old);
    assert.equal(f.observer.sequence, 0);
    assert.equal(f.observer.recentFrames.size, 0);
    assert.equal(f.boundaries[0].report.session, old);
    await f.listeners.get('device')(physical());
    assert.equal(f.published.length, 2);
    assert.equal(f.published[1].report.sequence, 1);
    assert.equal(f.published[1].report.observed_at_ms, BASE_TIME + 1);
    for (let i = 0; i < 32; i++) await f.listeners.get('mqttPublished')(bridge('online'));
    assert.equal(f.listeners.size, 3);
});

test('offline, unknown and malformed notices cannot restart; later online can', async () => {
    for (const payload of [JSON.stringify({state: 'offline'}), '{}', 'not-json', 'null']) {
        const f = fixture(); await f.observer.start();
        await f.listeners.get('device')(physical());
        await f.listeners.get('mqttPublished')({topic: bridge('').topic, payload});
        assert.equal(f.observer.running, false, payload);
        await f.listeners.get('device')(physical());
        assert.equal(f.published.length, 1);
        await f.listeners.get('mqttPublished')(bridge('online'));
        assert.equal(f.observer.running, true);
        await f.listeners.get('device')(physical());
        assert.equal(f.published[1].report.sequence, 1);
    }
});

test('unrelated state topics do not close or restart observations', async () => {
    const f = fixture(); await f.observer.start();
    const session = f.observer.session;
    await f.listeners.get('mqttPublished')({topic: 'other/bridge/state', payload: '{"state":"online"}'});
    assert.equal(f.observer.session, session);
    assert.equal(f.boundaries.length, 0);
});

test('real disconnected period stays closed until online with an available transport', async () => {
    const f = fixture(); await f.observer.start();
    await f.listeners.get('device')(physical());
    const old = f.observer.session;
    f.disconnect(); await f.listeners.get('device')(physical());
    await f.listeners.get('mqttPublished')(bridge('online'));
    assert.equal(f.observer.running, false);
    f.connect(); f.advance(5);
    await f.listeners.get('mqttPublished')(bridge('online'));
    assert.equal(f.observer.running, true);
    assert.notEqual(f.observer.session, old);
    await f.listeners.get('device')(physical());
    assert.equal(f.published[1].report.sequence, 1);
});

test('online during an old pending report waits for report and close marker, with no replay', async () => {
    let release, active = 0, peak = 0;
    const output = [], pending = new Promise(resolve => {release = resolve;});
    let firstReport = true;
    const f = fixture(async (topic, payload) => {
        active++; peak = Math.max(peak, active);
        if (topic.endsWith('/raw_v1') && firstReport) {firstReport = false; await pending;}
        output.push(JSON.parse(payload)); active--;
    });
    await f.observer.start();
    const oldSession = f.observer.session;
    const first = f.listeners.get('device')(physical());
    await f.listeners.get('mqttPublished')(bridge('online'));
    assert.equal(f.observer.running, false);
    await f.listeners.get('device')(physical());
    assert.equal(output.length, 0);
    release(); await first;
    assert.equal(peak, 1);
    assert.equal(output.length, 2);
    assert.equal(output[0].session, oldSession);
    assert.equal(output[1].source, 'observation_closed');
    assert.equal(output[1].session, oldSession);
    assert.equal(f.observer.running, true);
    f.advance(1); await f.listeners.get('device')(physical());
    assert.equal(output[2].sequence, 1);
    assert.notEqual(output[2].session, oldSession);
});

test('explicit stop cancels deferred recovery and even captured online callbacks', async () => {
    let release;
    const pending = new Promise(resolve => {release = resolve;});
    const f = fixture(async () => {await pending;}); await f.observer.start();
    const online = f.listeners.get('mqttPublished'), device = f.listeners.get('device');
    const first = device(physical());
    await online(bridge('online'));
    await f.observer.stop();
    await online(bridge('online'));
    release(); await first;
    assert.equal(f.observer.active, false);
    assert.equal(f.observer.running, false);
    assert.equal(f.observer.session, null);
    assert.equal(f.listeners.size, 0);
});

test('offline during a pending close notification cancels online recovery', async () => {
    let release;
    const pending = new Promise(resolve => {release = resolve;});
    const f = fixture(async topic => {if (topic.endsWith('/boundary_v1')) await pending;});
    await f.observer.start();
    const closing = f.listeners.get('mqttPublished')(bridge('online'));
    await f.listeners.get('mqttPublished')(bridge('offline'));
    release(); await closing;
    assert.equal(f.observer.running, false);
    assert.equal(f.observer.recoveryRequested, false);
});

test('close publication rejection does not recover on an unconfirmed boundary', async () => {
    const f = fixture(async () => {throw new Error('transport rejection');});
    await f.observer.start();
    await f.listeners.get('mqttPublished')(bridge('online'));
    assert.equal(f.observer.running, false);
    assert.equal(f.observer.recoveryRequested, false);
    assert.equal(f.listeners.size, 3);
});
