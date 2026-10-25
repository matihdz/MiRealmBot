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
        hooks: [], metadata: 0, memoryWrites: 0, failRead: false, guards: [], failAttach: false, attachAttempts: 0 };
    const memory = new Map();
    class Pointer {
        constructor(value) { this.value = Number(value); }
        add(n) { return new Pointer(this.value + n); }
        equals(other) { return this.value === other.value; }
        isNull() { return this.value === 0; }
        readPointer() { if (env.failRead) throw Error('unreadable world'); return memory.get(this.value); }
        toInt32() { return this.value | 0; }
        toString() { return '0x' + this.value.toString(16); }
    }
    const ptr = value => value instanceof Pointer ? value : new Pointer(value);
    const player = ptr(0x1000), world = ptr(0x2000), other = ptr(0x3000);
    const playerClass = ptr(10), worldClass = ptr(11), livingClass = ptr(12);
    memory.set(world.value + 0x48, player);
    env.association = { player, world, identity: 'world-player', map_id: 'map-1', gui_seen_at: 1000 };
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
            if (env.failAttach) throw Error('native attach failed');
            env.hooks.push(callbacks);
            return { detach() {} };
        } }
    });
    vm.runInContext(source, sandbox);
    const options = { enabled: true, active: true, confuse: true, quiet: false, ...overrides };
    const context = { module: { base: ptr(0x180000000) }, options,
        playerClass, worldClass, ownedHooks: [],
        isRunning: () => env.running, getAssociation: () => env.association,
        resolve() { env.metadata += 1; return livingClass; },
        instanceField(klass, name, type, offset) { env.metadata += 1; return { offset }; },
        typedMethod(klass, name, params, returnType, isStatic, rva) {
            env.metadata += 1; return { native_address: String(0x180000000 + rva), native_rva: '0x' + rva.toString(16) };
        },
        verifyBytes(rva, bytes) { env.guards.push({ rva, bytes }); return { rva, bytes }; },
        validObject(object, klass) {
            return object.equals(player) && klass.equals(playerClass) || object.equals(world) && klass.equals(worldClass);
        }
    };
    const bridge = sandbox.createDebuffsBridge(context);
    bridge.install();
    function enter({ receiver = player, caller = 0x18155f1fe } = {}) {
        const invocation = { returnAddress: ptr(caller) };
        env.hooks[0].onEnter.call(invocation, [receiver]);
        return invocation;
    }
    function leave(invocation, value = 1) {
        const result = { value, replacements: 0, toInt32() { return this.value; },
            replace(pointer) { this.value = pointer.toInt32(); this.replacements += 1; } };
        env.hooks[0].onLeave.call(invocation, result);
        return result;
    }
    return { env, bridge, context, player, world, other, memory, enter, leave };
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
console.log(checks + ' debuff scope checks passed (simulated native interfaces).');
