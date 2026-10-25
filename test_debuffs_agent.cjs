'use strict';
// Simulated Frida boundaries: exercises scope and return mutation without a game.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'debuffs_agent.js'), 'utf8');
let checks = 0;
function test(name, run) { run(); checks += 1; console.log('PASS ' + name); }

function setup(overrides = {}) {
    const env = { now: 1000, running: true, pid: 42, foregroundPid: 42, foreground: true,
        hooks: [], metadata: 0, memoryWrites: 0, failRead: false, guards: [], failAttach: false,
        attachAttempts: 0, fields: [], failQuietMetadata: false, failQuietRead: null,
        failConditionMetadata: false, conditionPointerReads: 0, conditionWordReads: 0,
        hookRvas: [], methods: [], failAttachAt: null, failMethodRva: null, failGuardRva: null,
        conditionWord1Reads: 0, conditionHeaderReads: 0 };
    const memory = new Map();
    class Pointer {
        constructor(value) { this.value = Number(value); }
        add(n) { return new Pointer(this.value + n); }
        equals(other) { return this.value === other.value; }
        isNull() { return this.value === 0; }
        readPointer() {
            if (this.value === 0x1250) env.conditionPointerReads += 1;
            if (env.failRead) throw Error('unreadable world');
            return memory.get(this.value);
        }
        readFloat() { if (env.failQuietRead === this.value) throw Error('unreadable MP'); return memory.get(this.value); }
        readS32() {
            if (this.value === 0x4020) env.conditionWordReads += 1;
            if (this.value === 0x4024) env.conditionWord1Reads += 1;
            if (env.failQuietRead === this.value) throw Error('unreadable condition');
            return memory.get(this.value);
        }
        readU8() { return memory.get(this.value); }
        readU64() {
            if (this.value === 0x4018) env.conditionHeaderReads += 1;
            return { toNumber: () => memory.get(this.value) };
        }
        toInt32() { return this.value | 0; }
        toString() { return '0x' + this.value.toString(16); }
    }
    const ptr = value => value instanceof Pointer ? value : new Pointer(value);
    const player = ptr(0x1000), world = ptr(0x2000), other = ptr(0x3000);
    const playerClass = ptr(10), worldClass = ptr(11), livingClass = ptr(12), visualClass = ptr(13);
    memory.set(world.value + 0x48, player);
    memory.set(player.value + 0x564, 305);
    memory.set(player.value + 0x744, 305);
    memory.set(player.value + 0x740, 0);
    memory.set(player.value + 0x250, ptr(0x4000));
    memory.set(0x4018, 3);
    memory.set(0x4020, 0);
    memory.set(0x4024, 0);
    env.association = { player, world, identity: 'world-player', map_id: 'map-1', gui_seen_at: 1000,
        map_name: 'Nexus', hp: 840, max_hp: 840 };
    const sandbox = vm.createContext({
        Date: { now: () => env.now }, ptr,
        Process: { id: env.pid, getModuleByName: name => ({ getExportByName: exported => exported }) },
        Memory: { alloc: () => ({ value: 0, writeU32(value) { this.value = value; }, readU32() { return this.value; } }) },
        NativeFunction: function (name) {
            if (name === 'GetForegroundWindow') return () => ptr(env.foreground ? 0x9000 : 0);
            if (name === 'GetWindowThreadProcessId') return (window, output) => { output.writeU32(env.foregroundPid); return 1; };
            throw Error('Unexpected native export');
        },
        Interceptor: { attach(address, callbacks) {
            env.attachAttempts += 1;
            if (env.failAttach || env.failAttachAt === env.attachAttempts) throw Error('native attach failed');
            env.hookRvas.push(address.value - 0x180000000);
            env.hooks.push(callbacks);
            return { detach() {} };
        } }
    });
    vm.runInContext(source, sandbox);
    const options = { enabled: true, active: true, confuse: true, quiet: false, ...overrides };
    const context = { module: { base: ptr(0x180000000) }, options,
        playerClass, worldClass, ownedHooks: [],
        isRunning: () => env.running, getAssociation: () => env.association,
        resolve(image, ns, name) { env.metadata += 1; return name === 'OGAIKIFOGLJ' ? visualClass : livingClass; },
        instanceField(klass, name, type, offset) {
            env.metadata += 1; env.fields.push({ name, type, offset });
            if (env.failQuietMetadata && name === 'FMHMGKEPIDN') throw Error('unexpected Quiet metadata');
            if (env.failConditionMetadata && name === 'COHCKAPOLCA') throw Error('unexpected condition metadata');
            return { offset };
        },
        typedMethod(klass, name, params, returnType, isStatic, rva) {
            env.metadata += 1;
            env.methods.push({ klass, name, params, returnType, isStatic, rva });
            if (env.failMethodRva === rva) throw Error('unexpected method profile');
            return { native_address: String(0x180000000 + rva), native_rva: '0x' + rva.toString(16) };
        },
        verifyBytes(rva, bytes) {
            env.guards.push({ rva, bytes });
            if (env.failGuardRva === rva) throw Error('unexpected code profile');
            return { rva, bytes };
        },
        validObject(object, klass) {
            return object.equals(player) && klass.equals(playerClass) || object.equals(world) && klass.equals(worldClass);
        }
    };
    const bridge = sandbox.createDebuffsBridge(context);
    bridge.install();
    function enter({ receiver = player, caller = 0x18155f1fe, getter = 0x777aa0 } = {}) {
        const hook = env.hookRvas.indexOf(getter);
        assert.ok(hook >= 0, 'Requested test getter must have an owned hook.');
        const invocation = { returnAddress: ptr(caller), testHook: hook };
        env.hooks[hook].onEnter.call(invocation, [receiver]);
        return invocation;
    }
    function leave(invocation, value = 1) {
        const result = { value, replacements: 0, toInt32() { return this.value; },
            replace(pointer) { this.value = pointer.toInt32(); this.replacements += 1; } };
        env.hooks[invocation.testHook].onLeave.call(invocation, result);
        return result;
    }
    function sample(now, word, values = {}) {
        env.now = now;
        if (env.association !== null) env.association.gui_seen_at = now;
        memory.set(0x4020, word);
        if ('word1' in values) memory.set(0x4024, values.word1);
        if ('mp' in values) memory.set(player.value + 0x564, values.mp);
        if ('mp_sync' in values) memory.set(player.value + 0x744, values.mp_sync);
        if ('mp_override' in values) memory.set(player.value + 0x740, values.mp_override);
        bridge.capture(env.association);
        return bridge.snapshot().quiet_observation;
    }
    return { env, bridge, context, player, world, other, memory, enter, leave, sample, ptr };
}

test('Disabled and empty modules install no hooks or metadata readers', () => {
    for (const options of [{ enabled: false }, { confuse: false }]) {
        const { env, bridge } = setup(options);
        assert.equal(env.metadata, 0);
        assert.equal(env.hooks.length, 0);
        assert.equal(bridge.snapshot().active, false);
    }
});
test('Unsupported Quiet is rejected even when the module is disabled', () => {
    for (const enabled of [false, true]) assert.throws(() => setup({ enabled, quiet: true }), /AntiQuiet no disponible/);
    assert.throws(() => setup({ enabled: 1 }), /booleans/);
    assert.throws(() => setup({ other: true }), /booleans/);
});
test('Active protection replaces only the verified local movement bool', () => {
    const { env, bridge, context, enter, leave } = setup();
    bridge.capture(env.association);
    const result = leave(enter());
    assert.equal(result.value, 0);
    assert.equal(result.replacements, 1);
    assert.equal(bridge.snapshot().blocked, 1);
    assert.equal(bridge.snapshot().detected, 1);
    assert.equal(bridge.bindings.condition_field_writes, false);
    assert.equal(bridge.bindings.mana_writes, false);
    assert.equal(context.ownedHooks.length, 1);
    bridge.install();
    assert.equal(env.hooks.length, 1);
    assert.deepEqual(env.guards.map(guard => guard.rva), [0x777aa0, 0x155f170, 0x155f1f9]);
});
test('Observation and an absent effect preserve the original native result', () => {
    const observe = setup({ active: false });
    assert.equal(observe.leave(observe.enter()).replacements, 0);
    assert.equal(observe.bridge.snapshot().detected, 1);
    assert.equal(observe.bridge.snapshot().blocked, 0);
    const active = setup();
    assert.equal(active.leave(active.enter(), 0).replacements, 0);
});
test('Other callers and other entities retain the game result', () => {
    const trial = setup();
    assert.equal(trial.leave(trial.enter({ caller: 0x18155f300 })).replacements, 0);
    assert.equal(trial.leave(trial.enter({ receiver: trial.other })).replacements, 0);
    assert.equal(trial.bridge.snapshot().checks, 0);
});
test('Stale, future, absent and mismatched world associations cannot modify movement', () => {
    for (const scenario of ['stale', 'future', 'absent', 'world-player']) {
        const trial = setup();
        if (scenario === 'stale') trial.env.now += 251;
        if (scenario === 'future') trial.env.now -= 1;
        if (scenario === 'absent') trial.env.association = null;
        if (scenario === 'world-player') trial.memory.set(trial.world.value + 0x48, trial.other);
        assert.equal(trial.leave(trial.enter()).replacements, 0, scenario);
    }
});
test('Foreground and running checks apply both before and after the native call', () => {
    for (const stage of ['enter', 'leave']) {
        for (const condition of ['foreground', 'foregroundPid', 'running']) {
            const trial = setup();
            let invocation;
            if (stage === 'leave') invocation = trial.enter();
            trial.env[condition] = condition === 'foregroundPid' ? 7 : false;
            if (stage === 'enter') invocation = trial.enter();
            assert.equal(trial.leave(invocation).replacements, 0, stage + ':' + condition);
        }
    }
});
test('Lifecycle, map changes and staleness invalidate an in-flight native call', () => {
    for (const change of ['invalidate', 'map', 'identity', 'age']) {
        const trial = setup(), invocation = trial.enter();
        if (change === 'invalidate') trial.bridge.invalidate();
        if (change === 'map') trial.env.association.map_id = 'map-2';
        if (change === 'identity') trial.env.association.identity = 'player-2';
        if (change === 'age') trial.env.now += 251;
        assert.equal(trial.leave(invocation).replacements, 0, change);
    }
});
test('A failed reader disables protection and exposes the reason without mutating results', () => {
    const trial = setup();
    trial.bridge.capture(trial.env.association);
    trial.env.failRead = true;
    assert.equal(trial.leave(trial.enter()).replacements, 0);
    trial.env.failRead = false;
    assert.match(trial.bridge.snapshot().error, /unreadable world/);
    assert.equal(trial.leave(trial.enter()).replacements, 0);
});
test('Unsigned bool high bytes do not misclassify a false low byte', () => {
    const trial = setup();
    assert.equal(trial.leave(trial.enter(), 0x100).replacements, 0);
});
test('A disabled bridge activates lazily and retains its binding and hook owner', () => {
    const trial = setup({ enabled: false }), bindings = trial.bridge.bindings;
    assert.equal(trial.env.metadata, 0);
    trial.bridge.configure({ enabled: true, active: true, confuse: true, quiet: false });
    assert.equal(trial.bridge.bindings, bindings);
    assert.ok(bindings.gate);
    assert.ok(bindings.code_guards);
    assert.equal(trial.env.hooks.length, 1);
    assert.equal(trial.context.ownedHooks.length, 1);
    const metadata = trial.env.metadata;
    const guards = bindings.code_guards;
    for (const enabled of [false, true, false, true]) {
        trial.bridge.configure({ enabled, active: true, confuse: true, quiet: false });
    }
    assert.equal(trial.env.metadata, metadata);
    assert.equal(trial.env.attachAttempts, 1);
    assert.equal(bindings.code_guards, guards);
    assert.equal(trial.leave(trial.enter()).replacements, 1);
});
test('Disabling or changing protection suppresses an in-flight return immediately', () => {
    for (const changes of [{ enabled: false }, { confuse: false }, { active: false }]) {
        const trial = setup(), invocation = trial.enter();
        trial.bridge.configure({ enabled: true, active: true, confuse: true, quiet: false, ...changes });
        assert.equal(trial.leave(invocation).replacements, 0);
        assert.equal(trial.leave(trial.enter()).replacements, 0);
        assert.equal(trial.bridge.bindings.writes, 'none');
        assert.equal(trial.env.attachAttempts, 1);
        trial.bridge.configure({ enabled: true, active: true, confuse: true, quiet: false });
        assert.equal(trial.leave(trial.enter()).replacements, 1);
    }
});
test('Invalid live options cannot partly change configuration or invalidate valid calls', () => {
    const trial = setup(), invocation = trial.enter();
    const before = trial.bridge.snapshot();
    for (const invalid of [
        { enabled: false, active: true, confuse: false, quiet: true },
        { enabled: false, active: true, confuse: 1, quiet: false },
        { enabled: false, active: true, quiet: false },
        { enabled: false, active: true, confuse: false, quiet: false, other: true }
    ]) assert.throws(() => trial.bridge.configure(invalid));
    assert.equal(trial.bridge.snapshot().enabled, before.enabled);
    assert.equal(trial.bridge.snapshot().confuse, before.confuse);
    assert.equal(trial.bridge.bindings.writes, 'Scoped boolean return only');
    assert.equal(trial.env.attachAttempts, 1);
    assert.equal(trial.leave(invocation).replacements, 1);
    const disabled = setup({ enabled: false });
    assert.throws(() => disabled.bridge.configure({ enabled: true, active: true, confuse: true, quiet: true }));
    assert.equal(disabled.env.metadata, 0);
    assert.equal(disabled.env.attachAttempts, 0);
});
test('Applied options are copied and equal options do not invalidate an existing call', () => {
    const trial = setup();
    trial.context.options.enabled = false;
    assert.equal(trial.leave(trial.enter()).replacements, 1);
    const next = { enabled: true, active: true, confuse: true, quiet: false };
    const invocation = trial.enter();
    trial.bridge.configure(next);
    next.active = false;
    assert.equal(trial.leave(invocation).replacements, 1);
    assert.equal(trial.bridge.snapshot().active, true);
});
test('First activation failures propagate for host cleanup and retain explicit failure status', () => {
    const trial = setup({ enabled: false });
    trial.env.failAttach = true;
    assert.throws(() => trial.bridge.configure({ enabled: true, active: true, confuse: true, quiet: false }), /native attach failed/);
    assert.equal(trial.context.ownedHooks.length, 0);
    assert.match(trial.bridge.snapshot().error, /native attach failed/);
    assert.equal(trial.env.attachAttempts, 1);
    trial.bridge.configure({ enabled: false, active: true, confuse: true, quiet: false });
    assert.equal(trial.env.attachAttempts, 1);
});
test('Quiet recording works alone, with typed fields and no hooks, foreground check or writes', () => {
    const trial = setup({ confuse: false, record_quiet: true });
    trial.env.foreground = false;
    const observation = trial.sample(1000, 0);
    assert.equal(trial.env.hooks.length, 0);
    assert.equal(trial.context.ownedHooks.length, 0);
    assert.equal(trial.env.guards.length, 0);
    assert.deepEqual(Object.fromEntries(trial.env.fields.map(field => [field.name, [field.type, field.offset]])), {
        OCLNLBHDEFK: ['FKALGHJIADI', 0x48], FMHMGKEPIDN: ['System.Single', 0x564],
        GGOFEAENCCG: ['System.Int32', 0x744], LHIKNJPECMB: ['System.Boolean', 0x740],
        COHCKAPOLCA: ['System.Int32[]', 0x250] });
    assert.equal(observation.current.mp, 305);
    assert.equal(observation.current.mp_sync, 305);
    assert.equal(observation.current.mp_override, false);
    assert.equal(observation.current.quiet, false);
    assert.equal(observation.current.hp, 840);
    assert.equal(observation.current.map_name, 'Nexus');
    assert.equal(observation.fresh, true);
    assert.equal(observation.available, true);
    assert.equal(observation.detections, 0);
    assert.equal(trial.bridge.snapshot().active, false);
    assert.equal(trial.env.memoryWrites, 0);
});
test('Quiet intervals retain only the last episode with correlated before, first, latest and after samples', () => {
    const trial = setup({ confuse: false, record_quiet: true });
    trial.sample(1000, 0, { mp: 305, mp_sync: 305 });
    let result = trial.sample(1050, 2, { mp: 0, mp_sync: 0 });
    assert.equal(result.revision, 1);
    assert.equal(result.detections, 1);
    assert.equal(result.last_episode.before.mp, 305);
    assert.equal(result.last_episode.first.mp, 0);
    assert.equal(result.last_episode.start_kind, 'false_to_true');
    result = trial.sample(1100, 2, { mp: 2, mp_sync: 0, mp_override: 1 });
    assert.equal(result.revision, 1);
    assert.equal(result.detections, 1);
    assert.equal(result.last_episode.latest.mp, 2);
    result = trial.sample(1150, 0, { mp: 3, mp_sync: 3, mp_override: 0 });
    assert.equal(result.revision, 2);
    assert.equal(result.last_episode.state, 'ended');
    assert.equal(result.last_episode.end_reason, 'quiet_cleared');
    assert.equal(result.last_episode.after.mp, 3);
    assert.equal(result.last_episode.latest.quiet, true);
    trial.sample(1200, 2);
    result = trial.sample(1250, 0);
    assert.equal(result.detections, 2);
    assert.equal(result.revision, 4);
    assert.equal(result.last_episode.id, 2);
    assert.equal(result.last_episode.before.timestamp_ms, 1150);
    assert.equal('episodes' in result, false);
});
test('An initially observed Quiet is not asserted to be a false-to-true onset', () => {
    const trial = setup({ confuse: false, record_quiet: true });
    const result = trial.sample(1000, 2);
    assert.equal(result.last_episode.start_kind, 'first_observed_true');
    assert.equal(result.last_episode.before, null);
});
test('Quiet snapshots do not create transitions, drain episodes, or expose stale samples as current', () => {
    const trial = setup({ confuse: false, record_quiet: true });
    trial.sample(1000, 2);
    trial.env.now = 1251;
    const stale = trial.bridge.snapshot().quiet_observation;
    assert.equal(stale.fresh, false);
    assert.equal(stale.current, null);
    assert.equal(stale.age_ms, 251);
    assert.equal(stale.revision, 1);
    assert.equal(stale.last_episode.state, 'active');
    stale.last_episode.first.mp = 99;
    assert.equal(trial.bridge.snapshot().quiet_observation.last_episode.first.mp, 305);
    const next = trial.sample(1300, 0);
    assert.equal(next.revision, 2);
    assert.equal(next.last_episode.state, 'interrupted');
    assert.equal(next.last_episode.end_reason, 'capture_gap');
    assert.equal(next.last_episode.after, null);
});
test('Quiet before samples require a continuous fresh association', () => {
    for (const kind of ['gap', 'identity', 'map', 'invalidate']) {
        const trial = setup({ confuse: false, record_quiet: true });
        trial.sample(1000, 0);
        if (kind === 'identity') trial.env.association.identity = 'other-player';
        if (kind === 'map') trial.env.association.map_id = 'map-2';
        if (kind === 'invalidate') trial.bridge.invalidate('world_lifecycle');
        const result = trial.sample(kind === 'gap' ? 1251 : 1050, 2);
        assert.equal(result.last_episode.before, null, kind);
        assert.equal(result.last_episode.start_kind, 'first_observed_true', kind);
    }
});
test('Quiet polling marks changed, future, stopped and stale associations unavailable without manufacturing transitions', () => {
    for (const kind of ['map', 'identity', 'future_capture', 'future_association', 'stale_association', 'stopped']) {
        const trial = setup({ confuse: false, record_quiet: true });
        trial.sample(1000, 2);
        if (kind === 'map') trial.env.association.map_id = 'map-2';
        if (kind === 'identity') trial.env.association.identity = 'other';
        if (kind === 'future_capture') trial.env.now = 999;
        if (kind === 'future_association') trial.env.association.gui_seen_at = 1001;
        if (kind === 'stale_association') trial.env.association.gui_seen_at = 749;
        if (kind === 'stopped') trial.env.running = false;
        const result = trial.bridge.snapshot().quiet_observation;
        assert.equal(result.fresh, false, kind);
        assert.equal(result.current, null, kind);
        assert.equal(result.available, false, kind);
        assert.equal(result.revision, 1, kind);
        assert.equal(result.last_episode.state, 'active', kind);
    }
});
test('Quiet map, lifecycle, missing association and configuration changes interrupt without inventing recovery', () => {
    for (const kind of ['identity', 'map', 'invalidate', 'absent', 'record_off', 'module_off']) {
        const trial = setup({ confuse: false, record_quiet: true });
        trial.sample(1000, 2);
        if (kind === 'identity') { trial.env.association.identity = 'other'; trial.sample(1050, 0); }
        if (kind === 'map') { trial.env.association.map_id = 'map-2'; trial.sample(1050, 0); }
        if (kind === 'invalidate') trial.bridge.invalidate('stopped');
        if (kind === 'absent') { trial.env.association = null; trial.bridge.capture(null); }
        if (kind.endsWith('_off')) trial.bridge.configure({ enabled: kind !== 'module_off', active: true,
            confuse: false, quiet: false, record_quiet: kind !== 'record_off' });
        const result = trial.bridge.snapshot().quiet_observation;
        assert.equal(result.revision, 2, kind);
        assert.equal(result.last_episode.state, 'interrupted', kind);
        assert.equal(result.last_episode.after, null, kind);
        if (kind.endsWith('_off')) assert.equal(result.current, null);
    }
});
test('Unknown and malformed Quiet reads preserve partial facts and do not disable Confuse', () => {
    for (const failure of ['null', 'empty', 'bad_length', 'unreadable', 'nan_mp', 'bad_bool']) {
        const trial = setup({ record_quiet: true });
        trial.sample(1000, 2);
        if (failure === 'null') trial.memory.set(trial.player.value + 0x250, trial.ptr(0));
        if (failure === 'empty') trial.memory.set(0x4018, 0);
        if (failure === 'bad_length') trial.memory.set(0x4018, Number.MAX_SAFE_INTEGER + 1);
        if (failure === 'unreadable') trial.env.failQuietRead = 0x4020;
        if (failure === 'nan_mp') trial.memory.set(trial.player.value + 0x564, NaN);
        if (failure === 'bad_bool') trial.memory.set(trial.player.value + 0x740, 2);
        const result = trial.sample(1050, 2);
        assert.ok(result.error, failure);
        assert.equal(result.available, false, failure);
        assert.equal(result.last_episode.state, 'interrupted', failure);
        assert.equal(result.last_episode.after, null, failure);
        if (['null', 'empty', 'bad_length', 'unreadable'].includes(failure)) {
            assert.equal(result.current.quiet, null, failure);
            assert.equal(result.current.mp, 305, failure);
        }
        assert.equal(trial.bridge.snapshot().error, null, failure);
        assert.equal(trial.leave(trial.enter()).replacements, 1, failure);
    }
});
test('Quiet metadata errors remain local while Confuse stays active', () => {
    const trial = setup({ enabled: false });
    trial.env.failQuietMetadata = true;
    trial.bridge.configure({ enabled: true, active: true, confuse: true, quiet: false, record_quiet: true });
    trial.bridge.capture(trial.env.association);
    const observation = trial.bridge.snapshot().quiet_observation;
    assert.match(observation.error, /unexpected Quiet metadata/);
    assert.equal(observation.current, null);
    assert.equal(observation.available, false);
    assert.equal(trial.bridge.snapshot().error, null);
    assert.equal(trial.leave(trial.enter()).replacements, 1);
});
test('Recording toggles retain the single Confuse hook and unrelated options retain the Quiet interval', () => {
    const trial = setup({ record_quiet: true });
    trial.sample(1000, 2);
    trial.bridge.configure({ enabled: true, active: true, confuse: false, quiet: false, record_quiet: true });
    let result = trial.sample(1050, 2);
    assert.equal(result.revision, 1);
    assert.equal(result.detections, 1);
    trial.bridge.configure({ enabled: true, active: true, confuse: true, quiet: false, record_quiet: true });
    result = trial.sample(1100, 2);
    assert.equal(result.revision, 1);
    assert.equal(trial.env.attachAttempts, 1);
    assert.equal(trial.leave(trial.enter()).replacements, 1);
});
test('Unsupported protection and invalid record_quiet reject before changing a valid recording', () => {
    const trial = setup({ record_quiet: true });
    const before = trial.sample(1000, 2);
    for (const changes of [{ quiet: true }, { record_quiet: 'true' }, { record_quiet: null }]) {
        assert.throws(() => trial.bridge.configure({ enabled: true, active: true,
            confuse: true, quiet: false, record_quiet: true, ...changes }));
    }
    const after = trial.bridge.snapshot().quiet_observation;
    assert.equal(after.revision, before.revision);
    assert.equal(after.last_episode.state, 'active');
    assert.equal(trial.bridge.snapshot().quiet_supported, false);
    assert.equal(trial.leave(trial.enter()).replacements, 1);
});
test('Confuse recording uses the pinned 0x400 condition independently of scoped movement results', () => {
    const trial = setup();
    trial.sample(1000, 0x80);
    let observation = trial.bridge.snapshot().confuse_observation;
    assert.equal(observation.current.confuse, false);
    assert.equal(observation.detections, 0);
    assert.equal(trial.bridge.snapshot().quiet_observation.enabled, false);
    trial.sample(1050, 0x400);
    observation = trial.bridge.snapshot().confuse_observation;
    assert.equal(observation.enabled, true);
    assert.equal(observation.current.confuse, true);
    assert.equal(observation.detections, 1);
    assert.equal(observation.last_episode.start_kind, 'false_to_true');
    assert.equal(observation.current.movement_checks, 0);
    assert.equal(observation.current.movement_detections, 0);
    assert.equal(observation.current.movement_blocks, 0);
    assert.equal(trial.leave(trial.enter()).replacements, 1);
    // The hook result does not synthesize raw condition observations. Captured
    // counters describe the sample's time, then update at the next GUI capture.
    assert.equal(trial.bridge.snapshot().confuse_observation.current.movement_blocks, 0);
    trial.sample(1100, 0x400);
    observation = trial.bridge.snapshot().confuse_observation;
    assert.equal(observation.revision, 1);
    assert.equal(observation.detections, 1);
    assert.equal(observation.current.movement_checks, 1);
    assert.equal(observation.current.movement_detections, 1);
    assert.equal(observation.current.movement_blocks, 1);
    assert.equal(observation.last_episode.latest.movement_blocks, 1);
    trial.sample(1150, 0);
    observation = trial.bridge.snapshot().confuse_observation;
    assert.equal(observation.last_episode.state, 'ended');
    assert.equal(observation.last_episode.end_reason, 'confuse_cleared');
    assert.equal(observation.last_episode.after.confuse, false);
    assert.equal(observation.last_episode.after.movement_blocks, 1);
    assert.equal(observation.last_episode.before.condition_word0, 0x80);
    assert.equal(observation.revision, 2);
    assert.equal(trial.env.hooks.length, 1);
    assert.equal(trial.env.fields.some(field => field.name === 'FMHMGKEPIDN'), false);
});
test('Confuse condition presence does not assert that a movement result was detected or blocked', () => {
    const trial = setup();
    trial.sample(1000, 0x400);
    const first = trial.bridge.snapshot().confuse_observation;
    assert.equal(first.last_episode.start_kind, 'first_observed_true');
    assert.equal(first.last_episode.before, null);
    assert.equal(first.current.movement_blocks, 0);
    assert.equal(trial.leave(trial.enter(), 0).replacements, 0);
    trial.sample(1050, 0x400);
    const next = trial.bridge.snapshot().confuse_observation;
    assert.equal(next.detections, 1);
    assert.equal(next.current.movement_checks, 1);
    assert.equal(next.current.movement_detections, 0);
    assert.equal(next.current.movement_blocks, 0);
    assert.equal(trial.bridge.snapshot().blocked, 0);
});
test('A scoped movement detection does not manufacture a GUI condition interval', () => {
    const trial = setup({ active: false });
    assert.equal(trial.leave(trial.enter()).replacements, 0);
    trial.sample(1000, 0);
    const observation = trial.bridge.snapshot().confuse_observation;
    assert.equal(observation.detections, 0);
    assert.equal(observation.last_episode, null);
    assert.equal(observation.current.confuse, false);
    assert.equal(observation.current.movement_checks, 1);
    assert.equal(observation.current.movement_detections, 1);
    assert.equal(observation.current.movement_blocks, 0);
});
test('Quiet and Confuse share one typed condition read while retaining independent intervals', () => {
    const trial = setup({ record_quiet: true });
    trial.sample(1000, 0);
    trial.sample(1050, 0x402);
    let result = trial.bridge.snapshot();
    assert.equal(result.quiet_observation.current.quiet, true);
    assert.equal(result.confuse_observation.current.confuse, true);
    assert.equal(result.quiet_observation.last_episode.first.timestamp_ms,
        result.confuse_observation.last_episode.first.timestamp_ms);
    assert.equal(trial.env.conditionPointerReads, 2);
    assert.equal(trial.env.conditionWordReads, 2);
    assert.equal(trial.env.fields.filter(field => field.name === 'COHCKAPOLCA').length, 1);
    const reads = trial.env.conditionWordReads;
    trial.bridge.snapshot(); trial.bridge.snapshot();
    assert.equal(trial.env.conditionWordReads, reads);
    trial.sample(1100, 0x400);
    result = trial.bridge.snapshot();
    assert.equal(result.quiet_observation.last_episode.state, 'ended');
    assert.equal(result.confuse_observation.last_episode.state, 'active');
    assert.equal(result.quiet_observation.revision, 2);
    assert.equal(result.confuse_observation.revision, 1);
});
test('Quiet MP read and metadata errors do not interrupt a valid Confuse observation', () => {
    for (const failure of ['nan_mp', 'invalid_bool', 'unreadable_mp', 'metadata']) {
        const trial = setup(failure === 'metadata' ? { enabled: false } : { record_quiet: true });
        if (failure === 'metadata') {
            trial.env.failQuietMetadata = true;
            trial.bridge.configure({ enabled: true, active: true, confuse: true, quiet: false, record_quiet: true });
        }
        trial.sample(1000, 0x402);
        if (failure === 'nan_mp') trial.memory.set(trial.player.value + 0x564, NaN);
        if (failure === 'invalid_bool') trial.memory.set(trial.player.value + 0x740, 2);
        if (failure === 'unreadable_mp') trial.env.failQuietRead = trial.player.value + 0x564;
        trial.sample(1050, 0x402);
        const result = trial.bridge.snapshot();
        assert.ok(result.quiet_observation.error, failure);
        assert.equal(result.confuse_observation.error, null, failure);
        assert.equal(result.confuse_observation.available, true, failure);
        assert.equal(result.confuse_observation.last_episode.state, 'active', failure);
        assert.equal(result.confuse_observation.revision, 1, failure);
        assert.equal(result.confuse_observation.detections, 1, failure);
        assert.equal(trial.leave(trial.enter()).replacements, 1, failure);
    }
});
test('Shared unknown condition reads interrupt observations without disabling the movement hook', () => {
    for (const failure of ['null', 'empty', 'bad_length', 'unreadable']) {
        const trial = setup({ record_quiet: true });
        trial.sample(1000, 0x402);
        if (failure === 'null') trial.memory.set(trial.player.value + 0x250, trial.ptr(0));
        if (failure === 'empty') trial.memory.set(0x4018, 0);
        if (failure === 'bad_length') trial.memory.set(0x4018, -1);
        if (failure === 'unreadable') trial.env.failQuietRead = 0x4020;
        trial.sample(1050, 0x402);
        const result = trial.bridge.snapshot();
        for (const name of ['confuse', 'quiet']) {
            const observation = result[name + '_observation'];
            assert.equal(observation.current[name], null, failure + ':' + name);
            assert.ok(observation.error, failure + ':' + name);
            assert.equal(observation.last_episode.state, 'interrupted', failure + ':' + name);
            assert.equal(observation.last_episode.end_reason, 'read_error', failure + ':' + name);
            assert.equal(observation.last_episode.after, null, failure + ':' + name);
        }
        assert.equal(result.error, null, failure);
        assert.equal(trial.leave(trial.enter()).replacements, 1, failure);
    }
});
test('Shared condition metadata failure is diagnostic and does not prevent scoped protection', () => {
    const trial = setup({ enabled: false });
    trial.env.failConditionMetadata = true;
    trial.bridge.configure({ enabled: true, active: true, confuse: true, quiet: false, record_quiet: true });
    trial.sample(1000, 0x402);
    const result = trial.bridge.snapshot();
    assert.match(result.confuse_observation.error, /unexpected condition metadata/);
    assert.match(result.quiet_observation.error, /unexpected condition metadata/);
    assert.equal(result.confuse_observation.current.confuse, null);
    assert.equal(result.quiet_observation.current.mp, 305);
    assert.equal(result.quiet_observation.current.quiet, null);
    assert.equal(result.error, null);
    assert.equal(trial.leave(trial.enter()).replacements, 1);
});
test('Confuse configuration, map and capture gaps interrupt intervals without declaring recovery', () => {
    for (const change of ['disable_confuse', 'disable_module', 'map', 'identity', 'gap', 'invalidate']) {
        const trial = setup({ record_quiet: true });
        trial.sample(1000, 0x400);
        if (change.startsWith('disable_')) trial.bridge.configure({ enabled: change !== 'disable_module',
            active: true, confuse: change !== 'disable_confuse', quiet: false, record_quiet: true });
        if (change === 'map') { trial.env.association.map_id = 'map-2'; trial.sample(1050, 0); }
        if (change === 'identity') { trial.env.association.identity = 'player-2'; trial.sample(1050, 0); }
        if (change === 'gap') trial.sample(1251, 0);
        if (change === 'invalidate') trial.bridge.invalidate('stopped');
        const observation = trial.bridge.snapshot().confuse_observation;
        assert.equal(observation.last_episode.state, 'interrupted', change);
        assert.equal(observation.last_episode.after, null, change);
        assert.equal(observation.revision, 2, change);
    }
});
test('Unrelated settings preserve a Confuse interval and snapshots retain fresh historical boundaries', () => {
    const trial = setup();
    trial.sample(1000, 0x400);
    trial.bridge.configure({ enabled: true, active: false, confuse: true, quiet: false, record_quiet: true });
    trial.sample(1050, 0x400);
    let result = trial.bridge.snapshot().confuse_observation;
    assert.equal(result.detections, 1);
    assert.equal(result.revision, 1);
    trial.env.now = 1301;
    result = trial.bridge.snapshot().confuse_observation;
    assert.equal(result.fresh, false);
    assert.equal(result.current, null);
    assert.equal(result.last_episode.state, 'active');
    assert.equal(result.revision, 1);
    trial.sample(1350, 0x400);
    result = trial.bridge.snapshot().confuse_observation;
    assert.equal(result.last_episode.start_kind, 'first_observed_true');
    assert.equal(result.last_episode.before, null);
    assert.equal(result.detections, 2);
    assert.equal(result.revision, 3);
    assert.equal(trial.env.hooks.length, 1);
});
test('Enabling Confuse after Quiet-only recording reuses condition metadata and retains the Quiet interval', () => {
    const trial = setup({ confuse: false, record_quiet: true });
    trial.sample(1000, 0x402);
    assert.equal(trial.env.hooks.length, 0);
    assert.equal(trial.bridge.snapshot().confuse_observation.current, null);
    trial.bridge.configure({ enabled: true, active: true, confuse: true, quiet: false, record_quiet: true });
    trial.sample(1050, 0x402);
    const result = trial.bridge.snapshot();
    assert.equal(result.quiet_observation.revision, 1);
    assert.equal(result.quiet_observation.detections, 1);
    assert.equal(result.confuse_observation.last_episode.start_kind, 'first_observed_true');
    assert.equal(result.confuse_observation.last_episode.before, null);
    assert.equal(result.confuse_observation.detections, 1);
    assert.equal(trial.env.hooks.length, 1);
    assert.equal(trial.env.fields.filter(field => field.name === 'COHCKAPOLCA').length, 1);
    assert.equal(trial.env.conditionPointerReads, 2);
    assert.equal(trial.env.conditionWordReads, 2);
});
const blindSites = [
    { getter: 0x7740a0, caller: 0x180e05652 },
    { getter: 0x7740a0, caller: 0x180e07fcd },
    { getter: 0x7740a0, caller: 0x180e0b86d },
    { getter: 0x77a2f0, caller: 0x180e0472d },
    { getter: 0x77a2f0, caller: 0x180e060dd },
    { getter: 0x77a2f0, caller: 0x180e0a6ed },
    { getter: 0x77a2f0, caller: 0x180e0b11d }
];
test('Blind defaults off and invalid live values reject without changing a valid scoped call', () => {
    const trial = setup(), invocation = trial.enter();
    assert.equal(trial.bridge.snapshot().blind, false);
    assert.equal(trial.bridge.snapshot().blind_observation.enabled, false);
    assert.equal(trial.env.hooks.length, 1);
    for (const blind of [1, null, 'true']) assert.throws(() => trial.bridge.configure({
        enabled: true, active: true, confuse: true, quiet: false, blind }));
    assert.equal(trial.env.hooks.length, 1);
    assert.equal(trial.leave(invocation).replacements, 1);
});
test('Blind-only protection installs two getters and accepts exactly the seven verified visual return sites', () => {
    const trial = setup({ confuse: false, blind: true });
    trial.sample(1000, 0x80);
    assert.equal(trial.env.hooks.length, 2);
    assert.equal(trial.context.ownedHooks.length, 2);
    assert.equal(trial.env.guards.length, 16);
    assert.equal(trial.env.methods.filter(method => method.returnType === 'System.Boolean').length, 2);
    const callers = trial.env.methods.filter(method => method.returnType === 'System.Void');
    assert.equal(callers.length, 7);
    assert.ok(callers.every(method => method.params.length === 0 && method.isStatic === false));
    for (const site of blindSites) {
        assert.equal(trial.leave(trial.enter(site)).replacements, 1, site.caller.toString(16));
        assert.equal(trial.leave(trial.enter({ ...site, caller: site.caller - 1 })).replacements, 0);
        assert.equal(trial.leave(trial.enter({ ...site, receiver: trial.other })).replacements, 0);
        assert.equal(trial.leave(trial.enter({ ...site, getter: site.getter === 0x7740a0 ? 0x77a2f0 : 0x7740a0 })).replacements, 0);
    }
    const result = trial.bridge.snapshot();
    assert.equal(result.blind, true);
    assert.equal(result.active, true);
    assert.match(result.status, /AntiBlind activo/);
    assert.equal(result.visual_checks, 7);
    assert.equal(result.visual_detections, 7);
    assert.equal(result.visual_blocks, 7);
    assert.equal(result.checks, 0);
    assert.equal(result.blocked, 0);
    assert.equal(result.blind_observation.current.visual_blocks, 0);
    assert.equal(trial.env.fields.some(field => field.name === 'FMHMGKEPIDN'), false);
    assert.equal(trial.bridge.bindings.condition_field_writes, false);
    assert.equal(trial.bridge.bindings.mana_writes, false);
    assert.equal(trial.bridge.bindings.blind_protection.runtime_visual_verified, false);
});
test('Both Blind getters recheck identity, map, freshness, focus, world link and lifecycle after entry', () => {
    for (const site of [blindSites[0], blindSites[3]]) {
        for (const phase of ['enter', 'leave']) {
            for (const change of ['stale', 'future', 'absent', 'world', 'focus', 'pid', 'running', 'invalidate']) {
                const trial = setup({ confuse: false, blind: true });
                let invocation;
                if (phase === 'leave') invocation = trial.enter(site);
                if (change === 'stale') trial.env.now += 251;
                if (change === 'future') trial.env.now -= 1;
                if (change === 'absent') trial.env.association = null;
                if (change === 'world') trial.memory.set(trial.world.value + 0x48, trial.other);
                if (change === 'focus') trial.env.foreground = false;
                if (change === 'pid') trial.env.foregroundPid = 7;
                if (change === 'running') trial.env.running = false;
                if (change === 'invalidate') {
                    // The association owner clears its value before lifecycle
                    // invalidation; the module does not own that shared value.
                    trial.env.association = null;
                    trial.bridge.invalidate('world_lifecycle');
                }
                if (phase === 'enter') invocation = trial.enter(site);
                assert.equal(trial.leave(invocation).replacements, 0,
                    site.getter.toString(16) + ':' + phase + ':' + change);
            }
        }
        for (const field of ['identity', 'map_id']) {
            const trial = setup({ confuse: false, blind: true }), invocation = trial.enter(site);
            trial.env.association[field] = 'changed';
            assert.equal(trial.leave(invocation).replacements, 0, field);
        }
    }
});
test('Blind recording distinguishes raw presence from verified visual detections and blocks', () => {
    const trial = setup({ confuse: false, blind: true });
    trial.sample(1000, 0x100);
    assert.equal(trial.bridge.snapshot().blind_observation.current.blind, false);
    trial.sample(1050, 0x80);
    let observation = trial.bridge.snapshot().blind_observation;
    assert.equal(observation.detections, 1);
    assert.equal(observation.last_episode.start_kind, 'false_to_true');
    assert.equal(observation.current.visual_blocks, 0);
    assert.equal(trial.leave(trial.enter(blindSites[0]), 0x100).replacements, 0);
    assert.equal(trial.leave(trial.enter(blindSites[3]), 1).replacements, 1);
    trial.sample(1100, 0x80);
    observation = trial.bridge.snapshot().blind_observation;
    assert.equal(observation.current.visual_checks, 2);
    assert.equal(observation.current.visual_detections, 1);
    assert.equal(observation.current.visual_blocks, 1);
    assert.equal(observation.detections, 1);
    assert.equal(observation.revision, 1);
    trial.sample(1150, 0);
    observation = trial.bridge.snapshot().blind_observation;
    assert.equal(observation.last_episode.state, 'ended');
    assert.equal(observation.last_episode.end_reason, 'blind_cleared');
    assert.equal(observation.last_episode.after.visual_blocks, 1);
    assert.equal(observation.last_episode.latest.blind, true);
    assert.equal(observation.revision, 2);
});
test('Blind observation mode counts eligible true results without replacing them', () => {
    const trial = setup({ confuse: false, blind: true, active: false });
    trial.sample(1000, 0x80);
    assert.equal(trial.leave(trial.enter(blindSites[0])).replacements, 0);
    const snapshot = trial.bridge.snapshot();
    assert.equal(snapshot.visual_checks, 1);
    assert.equal(snapshot.visual_detections, 1);
    assert.equal(snapshot.visual_blocks, 0);
    assert.equal(snapshot.blind_observation.detections, 1);
    assert.equal(snapshot.active, false);
});
test('All three condition observations share one read and Quiet MP failures spare both other intervals and protections', () => {
    const trial = setup({ blind: true, record_quiet: true });
    trial.sample(1000, 0x482);
    trial.memory.set(trial.player.value + 0x564, NaN);
    trial.sample(1050, 0x482);
    const result = trial.bridge.snapshot();
    assert.equal(trial.env.hooks.length, 3);
    assert.equal(trial.env.conditionPointerReads, 2);
    assert.equal(trial.env.conditionWordReads, 2);
    assert.equal(trial.env.fields.filter(field => field.name === 'COHCKAPOLCA').length, 1);
    assert.equal(result.quiet_observation.last_episode.state, 'interrupted');
    for (const effect of ['blind', 'confuse']) {
        assert.equal(result[effect + '_observation'].last_episode.state, 'active', effect);
        assert.equal(result[effect + '_observation'].error, null, effect);
    }
    assert.equal(trial.leave(trial.enter()).replacements, 1);
    assert.equal(trial.leave(trial.enter(blindSites[0])).replacements, 1);
    assert.equal(trial.leave(trial.enter(blindSites[3])).replacements, 1);
});
test('Unknown condition data interrupts Blind recording while its scoped visual protection remains independent', () => {
    const trial = setup({ blind: true });
    trial.sample(1000, 0x80);
    trial.memory.set(trial.player.value + 0x250, trial.ptr(0));
    trial.sample(1050, 0x80);
    const observation = trial.bridge.snapshot().blind_observation;
    assert.equal(observation.current.blind, null);
    assert.ok(observation.error);
    assert.equal(observation.last_episode.state, 'interrupted');
    assert.equal(observation.last_episode.after, null);
    assert.equal(trial.leave(trial.enter(blindSites[0])).replacements, 1);
    assert.equal(trial.leave(trial.enter()).replacements, 1);
});
test('Blind toggles invalidate in-flight calls and its own intervals without reinstalling hooks or interrupting Confuse', () => {
    const trial = setup({ blind: true, record_quiet: true });
    trial.sample(1000, 0x482);
    const invocation = trial.enter(blindSites[0]);
    const bindings = trial.bridge.bindings.blind_protection;
    trial.bridge.configure({ enabled: true, active: true, confuse: true, blind: false, quiet: false, record_quiet: true });
    assert.equal(trial.leave(invocation).replacements, 0);
    assert.equal(trial.leave(trial.enter(blindSites[0])).replacements, 0);
    assert.equal(trial.leave(trial.enter()).replacements, 1);
    let result = trial.bridge.snapshot();
    assert.equal(result.blind_observation.last_episode.state, 'interrupted');
    assert.equal(result.blind_observation.last_episode.after, null);
    assert.equal(result.confuse_observation.revision, 1);
    assert.equal(result.quiet_observation.revision, 1);
    trial.bridge.configure({ enabled: true, active: true, confuse: true, blind: true, quiet: false, record_quiet: true });
    trial.sample(1050, 0x482);
    result = trial.bridge.snapshot();
    assert.equal(result.blind_observation.last_episode.start_kind, 'first_observed_true');
    assert.equal(result.blind_observation.last_episode.before, null);
    assert.equal(result.confuse_observation.revision, 1);
    assert.equal(trial.env.attachAttempts, 3);
    assert.equal(trial.bridge.bindings.blind_protection, bindings);
    assert.equal(trial.leave(trial.enter(blindSites[3])).replacements, 1);
});
test('Blind profile failures are explicit and retain the existing Confuse owner for cleanup', () => {
    for (const failure of ['method', 'guard']) {
        const trial = setup();
        if (failure === 'method') trial.env.failMethodRva = 0xe055c0;
        if (failure === 'guard') trial.env.failGuardRva = 0xe0564d;
        assert.throws(() => trial.bridge.configure({ enabled: true, active: true, confuse: true,
            blind: true, quiet: false }), /unexpected .* profile/);
        assert.equal(trial.context.ownedHooks.length, 1);
        assert.match(trial.bridge.snapshot().error, /AntiBlind/);
        assert.equal(trial.leave(trial.enter()).replacements, 1);
    }
});
test('A partial Blind attachment retains its owned hook, leaves both Blind gates inactive and preserves Confuse', () => {
    const trial = setup();
    trial.env.failAttachAt = 3;
    assert.throws(() => trial.bridge.configure({ enabled: true, active: true, confuse: true,
        blind: true, quiet: false }), /native attach failed/);
    assert.equal(trial.context.ownedHooks.length, 2);
    assert.equal(trial.env.hooks.length, 2);
    assert.equal(trial.leave(trial.enter(blindSites[0])).replacements, 0);
    assert.equal(trial.leave(trial.enter()).replacements, 1);
    assert.match(trial.bridge.snapshot().error, /AntiBlind.*native attach failed/);
});
test('A Blind hook failure does not disable an independently valid Confuse hook', () => {
    const trial = setup({ blind: true });
    trial.env.failRead = true;
    assert.equal(trial.leave(trial.enter(blindSites[0])).replacements, 0);
    trial.env.failRead = false;
    assert.equal(trial.leave(trial.enter(blindSites[3])).replacements, 0);
    assert.equal(trial.leave(trial.enter()).replacements, 1);
    assert.match(trial.bridge.snapshot().error, /AntiBlind.*unreadable world/);
});
const silencedSites = [0x18155f551, 0x181570a37, 0x181570c43, 0x181564db6, 0x181564f9e]
    .map(caller => ({ getter: 0x777b20, caller }));
test('Silenced defaults off and invalid flags cannot change an existing scoped movement call', () => {
    const trial = setup(), invocation = trial.enter();
    assert.equal(trial.bridge.snapshot().silenced, false);
    assert.equal(trial.bridge.snapshot().silenced_observation.enabled, false);
    for (const silenced of [null, 1, 'true']) assert.throws(() => trial.bridge.configure({
        enabled: true, active: true, confuse: true, quiet: false, silenced }));
    assert.equal(trial.env.hooks.length, 1);
    assert.equal(trial.leave(invocation).replacements, 1);
    assert.equal(trial.env.conditionWord1Reads, 0);
});
test('Silenced-only installs the getter and limits replacements to five verified local ability return sites', () => {
    const trial = setup({ confuse: false, silenced: true });
    trial.sample(1000, 0, { word1: 0x10000 });
    assert.deepEqual(trial.env.hookRvas, [0x777b20]);
    assert.equal(trial.env.guards.length, 9);
    const callers = trial.env.methods.filter(method => method.rva !== 0x777b20);
    assert.deepEqual(callers.map(method => [method.name, [...method.params], method.returnType, method.isStatic]), [
        ['GCAEONBDGJF', ['System.Single', 'System.Single'], 'System.Boolean', false],
        ['PBABCOMDDPO', ['System.Single', 'System.Single', 'ELAINNINAMO'], 'System.Boolean', false],
        ['JBGFEHMHPGP', ['System.Single', 'System.Single', 'ELAINNINAMO'], 'System.Boolean', false] ]);
    for (const site of silencedSites) {
        assert.equal(trial.leave(trial.enter(site)).replacements, 1);
        assert.equal(trial.leave(trial.enter({ ...site, caller: site.caller - 1 })).replacements, 0);
        assert.equal(trial.leave(trial.enter({ ...site, receiver: trial.other })).replacements, 0);
    }
    assert.equal(trial.leave(trial.enter({ getter: 0x777b20, caller: 0x180e1abcc })).replacements, 0);
    const result = trial.bridge.snapshot();
    assert.equal(result.active, true);
    assert.match(result.status, /AntiSilenced/);
    assert.equal(result.ability_gate_checks, 5);
    assert.equal(result.ability_gate_detections, 5);
    assert.equal(result.ability_gate_bypasses, 5);
    assert.equal(result.checks, 0);
    assert.equal(result.visual_checks, 0);
    assert.equal(result.silenced_experimental, true);
    assert.equal(result.server_acceptance_verified, false);
    assert.equal(trial.bridge.bindings.silenced_protection.server_acceptance_verified, false);
    assert.equal(trial.bridge.bindings.mana_writes, false);
    assert.equal(trial.bridge.bindings.cooldown_writes, false);
    assert.equal(trial.bridge.bindings.network_changes, false);
});
test('Silenced records word1 rather than a matching word0 mask and keeps bypasses separate from intervals', () => {
    const trial = setup({ confuse: false, silenced: true });
    trial.sample(1000, 0x10000, { word1: 0 });
    let observation = trial.bridge.snapshot().silenced_observation;
    assert.equal(observation.current.silenced, false);
    assert.equal(observation.current.condition_word1, 0);
    assert.equal('condition_word0' in observation.current, false);
    assert.equal(observation.detections, 0);
    trial.sample(1050, 0, { word1: 0x10000 });
    observation = trial.bridge.snapshot().silenced_observation;
    assert.equal(observation.current.silenced, true);
    assert.equal(observation.current.ability_gate_bypasses, 0);
    assert.equal(observation.last_episode.start_kind, 'false_to_true');
    assert.equal(observation.last_episode.before.condition_word1, 0);
    assert.equal(trial.leave(trial.enter(silencedSites[0]), 0x100).replacements, 0);
    assert.equal(trial.leave(trial.enter(silencedSites[4])).replacements, 1);
    trial.sample(1100, 0, { word1: 0x10000 });
    observation = trial.bridge.snapshot().silenced_observation;
    assert.equal(observation.detections, 1);
    assert.equal(observation.revision, 1);
    assert.equal(observation.current.ability_gate_checks, 2);
    assert.equal(observation.current.ability_gate_detections, 1);
    assert.equal(observation.current.ability_gate_bypasses, 1);
    trial.sample(1150, 0, { word1: 0 });
    observation = trial.bridge.snapshot().silenced_observation;
    assert.equal(observation.last_episode.end_reason, 'silenced_cleared');
    assert.equal(observation.last_episode.after.silenced, false);
    assert.equal(observation.last_episode.after.ability_gate_bypasses, 1);
    assert.equal(observation.revision, 2);
    assert.equal(trial.env.conditionWordReads, 0);
    assert.equal(trial.env.conditionWord1Reads, 4);
});
test('The shared reader preserves word0 observations when word1 is missing or unreadable', () => {
    for (const problem of ['short_array', 'unreadable_word1']) {
        const trial = setup({ blind: true, silenced: true, record_quiet: true });
        trial.sample(1000, 0x482, { word1: 0x10000 });
        if (problem === 'short_array') trial.memory.set(0x4018, 1);
        if (problem === 'unreadable_word1') trial.env.failQuietRead = 0x4024;
        trial.sample(1050, 0x482, { word1: 0x10000 });
        const result = trial.bridge.snapshot();
        for (const name of ['quiet', 'confuse', 'blind']) {
            const observation = result[name + '_observation'];
            assert.equal(observation.error, null, name + ':' + problem);
            assert.equal(observation.last_episode.state, 'active', name + ':' + problem);
            assert.equal(observation.revision, 1, name + ':' + problem);
            assert.equal(observation.current.condition_word0, 0x482, name + ':' + problem);
        }
        assert.equal(result.silenced_observation.current.silenced, null);
        assert.match(result.silenced_observation.error, /condition_word1/);
        assert.equal(result.silenced_observation.last_episode.state, 'interrupted');
        assert.equal(result.silenced_observation.last_episode.after, null);
        assert.equal(trial.env.conditionPointerReads, 2);
        assert.equal(trial.env.conditionHeaderReads, 2);
        assert.equal(trial.env.conditionWordReads, 2);
        assert.equal(trial.env.conditionWord1Reads, problem === 'short_array' ? 1 : 2);
        assert.equal(trial.leave(trial.enter()).replacements, 1);
        assert.equal(trial.leave(trial.enter(blindSites[0])).replacements, 1);
    }
});
test('Word0 and MP failures do not erase a separately valid Silenced observation', () => {
    for (const problem of ['word0', 'mp']) {
        const trial = setup({ blind: true, silenced: true, record_quiet: true });
        trial.sample(1000, 0x482, { word1: 0x10000 });
        if (problem === 'word0') trial.env.failQuietRead = 0x4020;
        if (problem === 'mp') trial.memory.set(trial.player.value + 0x564, NaN);
        trial.sample(1050, 0x482, { word1: 0x10000 });
        const result = trial.bridge.snapshot();
        assert.ok(result.quiet_observation.error);
        assert.equal(result.silenced_observation.error, null);
        assert.equal(result.silenced_observation.last_episode.state, 'active');
        assert.equal(result.silenced_observation.detections, 1);
        assert.equal(result.silenced_observation.revision, 1);
        assert.equal(result.silenced_observation.current.ability_gate_bypasses, 0);
        assert.equal(result.server_acceptance_verified, false);
        assert.equal(trial.leave(trial.enter(silencedSites[0])).replacements, 1);
    }
});
test('Silenced getter rechecks running, focus, association freshness and identity on both sides of the call', () => {
    for (const phase of ['enter', 'leave']) {
        for (const problem of ['stale', 'future', 'focus', 'pid', 'running', 'world', 'absent']) {
            const trial = setup({ confuse: false, silenced: true });
            let invocation;
            if (phase === 'leave') invocation = trial.enter(silencedSites[0]);
            if (problem === 'stale') trial.env.now += 251;
            if (problem === 'future') trial.env.now -= 1;
            if (problem === 'focus') trial.env.foreground = false;
            if (problem === 'pid') trial.env.foregroundPid = 1;
            if (problem === 'running') trial.env.running = false;
            if (problem === 'world') trial.memory.set(trial.world.value + 0x48, trial.other);
            if (problem === 'absent') trial.env.association = null;
            if (phase === 'enter') invocation = trial.enter(silencedSites[0]);
            assert.equal(trial.leave(invocation).replacements, 0, phase + ':' + problem);
        }
    }
    for (const field of ['map_id', 'identity']) {
        const trial = setup({ confuse: false, silenced: true }), invocation = trial.enter(silencedSites[4]);
        trial.env.association[field] = 'changed';
        assert.equal(trial.leave(invocation).replacements, 0, field);
    }
});
test('Disabling Silenced stops in-flight replacements and interrupts only its own interval without reinstalling hooks', () => {
    const trial = setup({ blind: true, silenced: true, record_quiet: true });
    trial.sample(1000, 0x482, { word1: 0x10000 });
    const invocation = trial.enter(silencedSites[0]);
    trial.bridge.configure({ enabled: true, active: true, confuse: true, blind: true,
        silenced: false, quiet: false, record_quiet: true });
    assert.equal(trial.leave(invocation).replacements, 0);
    assert.equal(trial.leave(trial.enter(silencedSites[0])).replacements, 0);
    let result = trial.bridge.snapshot();
    assert.equal(result.silenced_observation.last_episode.state, 'interrupted');
    assert.equal(result.silenced_observation.last_episode.after, null);
    for (const name of ['quiet', 'confuse', 'blind']) assert.equal(result[name + '_observation'].revision, 1);
    trial.bridge.configure({ enabled: true, active: true, confuse: true, blind: true,
        silenced: true, quiet: false, record_quiet: true });
    trial.sample(1050, 0x482, { word1: 0x10000 });
    result = trial.bridge.snapshot();
    assert.equal(result.silenced_observation.last_episode.start_kind, 'first_observed_true');
    assert.equal(result.silenced_observation.last_episode.before, null);
    assert.equal(trial.env.attachAttempts, 4);
    assert.equal(trial.leave(trial.enter(silencedSites[4])).replacements, 1);
});
test('Silenced maps and stale gaps interrupt without inventing recovery or reusing an old before sample', () => {
    for (const problem of ['map', 'gap', 'lifecycle']) {
        const trial = setup({ confuse: false, silenced: true });
        trial.sample(1000, 0, { word1: 0x10000 });
        if (problem === 'map') { trial.env.association.map_id = 'new-map'; trial.sample(1050, 0, { word1: 0 }); }
        if (problem === 'gap') trial.sample(1251, 0, { word1: 0 });
        if (problem === 'lifecycle') trial.bridge.invalidate('stopped');
        const result = trial.bridge.snapshot().silenced_observation;
        assert.equal(result.last_episode.state, 'interrupted', problem);
        assert.equal(result.last_episode.after, null, problem);
        assert.equal(result.revision, 2, problem);
    }
});
test('Silenced native guard failure is scoped while movement protection retains its owned hook', () => {
    const trial = setup();
    trial.env.failGuardRva = 0x1570520;
    assert.throws(() => trial.bridge.configure({ enabled: true, active: true, confuse: true,
        silenced: true, quiet: false }), /unexpected code profile/);
    assert.equal(trial.context.ownedHooks.length, 1);
    assert.match(trial.bridge.snapshot().error, /AntiSilenced/);
    assert.equal(trial.leave(trial.enter()).replacements, 1);
});
test('Recording without active changes counts Silenced gates while making no ability-acceptance claim', () => {
    const trial = setup({ confuse: false, silenced: true, active: false });
    trial.sample(1000, 0, { word1: 0x10000 });
    assert.equal(trial.leave(trial.enter(silencedSites[0])).replacements, 0);
    const result = trial.bridge.snapshot();
    assert.equal(result.ability_gate_checks, 1);
    assert.equal(result.ability_gate_detections, 1);
    assert.equal(result.ability_gate_bypasses, 0);
    assert.equal(result.silenced_observation.detections, 1);
    assert.equal(result.server_acceptance_verified, false);
});
console.log(checks + ' debuff scope checks passed (simulated native interfaces).');
