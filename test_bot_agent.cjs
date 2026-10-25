'use strict';
// Executes production RPC/configuration and prediction-hook bodies in a VM.
// Metadata, native memory, and child module owners are simulated: this verifies
// orchestration/lifecycle semantics, not compatibility or behavior in the game.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const main = fs.readFileSync(path.join(__dirname, 'bot_agent.js'), 'utf8');
const aim = fs.readFileSync(path.join(__dirname, 'autoaim_agent.js'), 'utf8');
function selectedBody(begin, end) {
    const first = main.indexOf(begin), last = main.indexOf(end, first + begin.length);
    assert.ok(first >= 0 && last > first, 'Production body delimiters must remain explicit.');
    assert.equal(main.indexOf(begin, first + begin.length), -1, 'Production body must have one owner.');
    return main.slice(first, last);
}
const configure = selectedBody('        configureRuntime = nextOptions => {', '\n        };') + '\n        };';
const prediction = selectedBody('    function usable() {', '\n    try {\n        ownedHooks.push(Interceptor.attach(ptr(clean.native_address)');
const describePrediction = selectedBody('    function describePrediction(enabled) {', '\n    describePrediction(requestedPrediction);');
let checks = 0;
function test(name, run) { run(); checks += 1; console.log('PASS ' + name); }
const defaults = () => ({ prediction_enabled: false, detect_game_keys: false,
    autoaim: { enabled: false, active: true, mode: 'close_to_mouse', activation: 'autofire',
        extrapolation: 302, mouse_radius: 4, prioritize_bosses: false, ignore_walls: false },
    debuffs: { enabled: false, active: true, confuse: true, quiet: false } });

function harness() {
    class Pointer {
        constructor(value) { this.value = Number(value); }
        add(offset) { return new Pointer(this.value + offset); }
        isNull() { return this.value === 0; }
        equals(other) { return this.value === other.value; }
        toInt32() { return this.value | 0; }
        toString() { return String(this.value); }
    }
    const env = { hooks: [], creates: { aim: 0, keys: 0 }, configurations: [], prepared: 0,
        attached: false, attachCount: 0, detachCount: 0, failAttach: false, failDebuffs: false,
        failDetachId: null, detached: [], emitted: [], now: 1000 };
    const ptr = value => new Pointer(value);
    const context = vm.createContext({ env, ptr, rpc: { exports: {} },
        Date: { now: () => env.now },
        Interceptor: { attach(address, callbacks) {
            const id = env.hooks.length;
            const hook = { id, address: address.toInt32(), callbacks, detached: false,
                detach() {
                    if (env.failDetachId === id) throw Error('detach failed');
                    this.detached = true;
                    env.detached.push(id);
                } };
            env.hooks.push(hook);
            return hook;
        } }
    });
    vm.runInContext(aim + '\n' + main, context);
    vm.runInContext(`
        const api = {
            thread_current: () => ptr(env.attached ? 1 : 0),
            thread_attach: () => { env.attachCount += 1; if (env.failAttach) return ptr(0); env.attached = true; return ptr(7); },
            thread_detach: () => { env.detachCount += 1; env.attached = false; }
        };
        const domain = ptr(5);
        const module = { base: ptr(0x180000000) };
        const playerClass = ptr(30), projectileClass = ptr(31);
        const collision = { native_address: '100' }, effectiveDamage = { native_address: '101' },
            baseDamage = { native_address: '102' }, healthSync = { native_address: '103' };
        let predictionPrepared = false, predictionHooksInstalled = false;
        const bindings = { prediction: { enabled: false, source: null, accepted_causes: [] }, restrictions: {} };
        ${describePrediction}
        const bridgeContext = { ownedHooks };
        function validObject() { return true; }
        function readEventFrame() { return { map_id: 'map-1' }; }
        function appendEvent(...values) { env.emitted.push(values); }
        function preparePrediction() { if (!predictionPrepared) { env.prepared += 1; predictionPrepared = true; } }
        function owner(kind, options) {
            let installed = false;
            return { bindings: { kind }, install() {
                assertAttached();
                if (installed) return;
                ownedHooks.push(Interceptor.attach(ptr(kind === 'aim' ? 200 : 201), {})); installed = true;
            }, configure(next) {
                assertAttached(); env.configurations.push({kind, options: {...next}});
            }, invalidate() {}, snapshot() { return {}; } };
        }
        function assertAttached() { if (!env.attached) throw Error('Metadata owner called without a thread attachment'); }
        createAutoAimBridge = context => { assertAttached(); env.creates.aim += 1; return owner('aim', context.options); };
        createGameBindingsBridge = () => { assertAttached(); env.creates.keys += 1; return owner('keys', {}); };
        debuffsBridge = { bindings: {}, invalidate() {}, snapshot() { return {}; }, configure(options) {
            assertAttached();
            if (env.failDebuffs || options.quiet) throw Error('Debuffs configuration rejected');
            env.configurations.push({kind: 'debuffs', options: {...options}});
        } };
        ${prediction}
        ${configure}
        running = true;
        sequence = 17; eventId = 9; epoch = 4;
        latest = { captured_at: 1000, frame: { sequence: 17, map_id: 'map-1' } };
        events = [{ event_id: 9, kind: 'health_sync', captured_at: 999, frame: { map_id: 'map-1' } }];
        diagnostics = { collision_calls: 0, local_calculations: 0, matched_damage_entries: 0,
            final_damage_events: 0, health_sync_events: 0, zero_damage_hits: 0, queue_overflows: 0 };
        globalThis.inspect = () => ({ running, sequence, eventId, epoch, predictionEnabled,
            scopeGeneration, scopeThreads: threadScopes.size, events: events.length, hooks: ownedHooks.length,
            startOptions, captureError, bindings });
        globalThis.markScope = () => threadScopes.set(42, { collisions: [], calculations: [], damage_calls: [] });
        globalThis.captureFailure = () => { captureError = 'capture failed'; };
    `, context);
    return { env, ptr, rpc: context.rpc.exports, inspect: context.inspect, markScope: context.markScope,
        captureFailure: context.captureFailure };
}

test('Live configuration retains sequence, event IDs, queued events and lifecycle epoch', () => {
    const trial = harness(), options = defaults();
    options.autoaim.enabled = true;
    options.detect_game_keys = true;
    assert.equal(trial.rpc.configure(options).configured, true);
    const state = trial.inspect();
    assert.equal(state.sequence, 17);
    assert.equal(state.eventId, 9);
    assert.equal(state.epoch, 4);
    assert.equal(state.events, 1);
    assert.equal(state.running, true);
    const polled = trial.rpc.poll();
    assert.equal(polled.sequence, 17);
    assert.equal(polled.events[0].event_id, 9);
    assert.equal(trial.env.attachCount, 1);
    assert.equal(trial.env.detachCount, 1);
});
test('Lazy module creation and prediction installation retain one native owner per hook', () => {
    const trial = harness(), options = defaults();
    options.prediction_enabled = true;
    options.autoaim.enabled = true;
    options.detect_game_keys = true;
    trial.rpc.configure(options);
    const hookCount = trial.env.hooks.length;
    assert.equal(hookCount, 7); // Five real production prediction hooks; two simulated child hooks.
    for (const enabled of [false, true, false, true]) {
        trial.rpc.configure({ ...options, prediction_enabled: enabled,
            autoaim: { ...options.autoaim, enabled }, detect_game_keys: enabled });
    }
    assert.equal(trial.env.hooks.length, hookCount);
    assert.equal(trial.env.prepared, 1);
    assert.equal(trial.env.creates.aim, 1);
    assert.equal(trial.env.creates.keys, 1);
    assert.equal(trial.inspect().hooks, hookCount);
    trial.rpc.stop();
    assert.equal(trial.inspect().hooks, 0);
    assert.equal(trial.env.detached.length, hookCount);
});
test('Disabling prediction clears an in-flight production collision scope without a capture error', () => {
    const trial = harness(), options = { ...defaults(), prediction_enabled: true };
    trial.rpc.configure(options);
    const callback = trial.env.hooks[0].callbacks;
    const call = { threadId: 42 };
    callback.onEnter.call(call, [trial.ptr(800)]);
    assert.equal(trial.inspect().scopeThreads, 1);
    trial.rpc.configure({ ...options, prediction_enabled: false });
    assert.equal(trial.inspect().scopeThreads, 0);
    callback.onLeave.call(call, trial.ptr(1));
    assert.equal(trial.inspect().captureError, null);
    assert.equal(trial.env.emitted.length, 0);
    const later = { threadId: 42 };
    callback.onEnter.call(later, [trial.ptr(800)]);
    assert.equal(trial.inspect().scopeThreads, 0);
});
test('Prediction scopes survive unrelated configuration and invalidate only when prediction changes', () => {
    const trial = harness(), options = defaults();
    trial.markScope();
    const original = trial.inspect().scopeGeneration;
    trial.rpc.configure(options);
    assert.equal(trial.inspect().scopeGeneration, original);
    assert.equal(trial.inspect().scopeThreads, 1);
    trial.rpc.configure({ ...options, prediction_enabled: true });
    assert.equal(trial.inspect().scopeThreads, 0);
    assert.ok(trial.inspect().scopeGeneration > original);
});
test('Prediction provenance follows the enabled mode after each live change', () => {
    const trial = harness(), options = defaults();
    for (const enabled of [true, false, true]) {
        trial.rpc.configure({ ...options, prediction_enabled: enabled });
        const bindings = trial.inspect().bindings;
        assert.equal(bindings.prediction.enabled, enabled);
        assert.equal(bindings.prediction.source !== null, enabled);
        assert.equal(bindings.prediction.accepted_causes.length, enabled ? 1 : 0);
        assert.equal(bindings.health_limitations.includes('is disabled'), !enabled);
        assert.equal(bindings.tracked_health_source.includes('final projectile damage'), enabled);
    }
});
test('Configuration preserves a metadata thread attachment it did not create', () => {
    const trial = harness();
    trial.env.attached = true;
    trial.rpc.configure(defaults());
    assert.equal(trial.env.attachCount, 0);
    assert.equal(trial.env.detachCount, 0);
    assert.equal(trial.env.attached, true);
});
test('Invalid bridge and aim options reject before acquiring threads or installing owners', () => {
    const trial = harness(), options = defaults();
    assert.throws(() => trial.rpc.configure({ ...options, prediction_enabled: 1 }), /Invalid bridge/);
    assert.throws(() => trial.rpc.configure({ ...options, autoaim: { ...options.autoaim, mouse_radius: 0 } }), /Close to Mouse/);
    assert.equal(trial.env.attachCount, 0);
    assert.equal(trial.env.hooks.length, 0);
    assert.equal(trial.inspect().events, 1);
});
test('Thread and child configuration errors propagate and retain hooks for host cleanup', () => {
    const trial = harness(), options = defaults();
    trial.env.failAttach = true;
    assert.throws(() => trial.rpc.configure(options), /configuration metadata thread/);
    assert.equal(trial.env.detachCount, 0);
    trial.env.failAttach = false;
    trial.env.failDebuffs = true;
    options.autoaim.enabled = true;
    assert.throws(() => trial.rpc.configure(options), /Debuffs configuration rejected/);
    assert.equal(trial.env.attached, false);
    assert.equal(trial.env.detachCount, 1);
    assert.equal(trial.inspect().hooks, 1);
    trial.rpc.stop();
    assert.equal(trial.inspect().hooks, 0);
});
test('Failed native cleanup retains its owner for a later stop retry', () => {
    const trial = harness(), options = defaults();
    options.autoaim.enabled = true;
    trial.rpc.configure(options);
    trial.env.failDetachId = 0;
    assert.throws(() => trial.rpc.stop(), /Could not detach owned hooks/);
    assert.equal(trial.inspect().hooks, 1);
    assert.equal(trial.inspect().running, false);
    trial.env.failDetachId = null;
    trial.rpc.stop();
    assert.equal(trial.inspect().hooks, 0);
});
test('The public configure RPC rejects stopped and failed captures', () => {
    const stopped = harness();
    stopped.rpc.stop();
    assert.throws(() => stopped.rpc.configure(defaults()), /not ready/);
    const failed = harness();
    failed.captureFailure();
    assert.throws(() => failed.rpc.configure(defaults()), /not ready/);
    assert.equal(failed.env.attachCount, 0);
});
console.log(checks + ' production configuration checks passed (simulated native and child boundaries).');
