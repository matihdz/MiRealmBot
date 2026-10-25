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
const debuffs = fs.readFileSync(path.join(__dirname, 'debuffs_agent.js'), 'utf8');
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
        readPointer() { env.targetReads += 1; return new Pointer(env.memory.get(this.value)); }
        readFloat() { env.targetReads += 1; return env.memory.get(this.value); }
        readS32() { env.targetReads += 1; return env.memory.get(this.value); }
        readU8() { env.targetReads += 1; return env.memory.get(this.value); }
        readU64() { env.targetReads += 1; return { toNumber: () => env.memory.get(this.value) }; }
    }
    const env = { hooks: [], creates: { aim: 0, keys: 0 }, configurations: [], prepared: 0,
        attached: false, attachCount: 0, detachCount: 0, failAttach: false, failDebuffs: false,
        failDetachId: null, detached: [], emitted: [], now: 1000,
        targetReads: 0, memory: new Map(), failSnapshot: false };
    const ptr = value => new Pointer(value);
    const context = vm.createContext({ env, ptr, rpc: { exports: {} },
        Date: { now: () => env.now },
        Process: { id: 42, getModuleByName: () => ({ getExportByName: name => name }) },
        Memory: { alloc: () => ({ value: 0, writeU32(value) { this.value = value; }, readU32() { return this.value; } }) },
        NativeFunction: function(name) {
            if (name === 'GetForegroundWindow') return () => ptr(0x9000);
            if (name === 'GetWindowThreadProcessId') return (window, output) => { output.writeU32(42); return 1; };
            throw Error('Unexpected native export');
        },
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
    vm.runInContext(aim + '\n' + debuffs + '\n' + main, context);
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
        debuffsBridge = { bindings: {}, invalidate() {}, snapshot() {
            if (env.failSnapshot) throw Error('diagnostic snapshot failed');
            return {};
        }, configure(options) {
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
        globalThis.beginDebuffEpisode = (effect = 'quiet') => {
            const player = ptr(0x1000), world = ptr(0x2000), worldClass = ptr(32);
            const effects = effect === 'all' ? ['confuse', 'blind', 'quiet', 'silenced'] :
                effect === 'three' ? ['confuse', 'blind', 'quiet'] :
                effect === 'both' ? ['confuse', 'quiet'] : [effect];
            const word = (effects.includes('confuse') ? 0x400 : 0) |
                (effects.includes('blind') ? 0x80 : 0) | (effects.includes('quiet') ? 2 : 0);
            for (const [address, value] of [[0x2048, 0x1000], [0x1564, 0], [0x1744, 0],
                [0x1740, 0], [0x1250, 0x4000], [0x4018, 3],
                [0x4020, word], [0x4024, effects.includes('silenced') ? 0x10000 : 0]]) env.memory.set(address, value);
            activeAssociation = { player, world, identity: 'player-world', map_id: 'map-1',
                gui_seen_at: env.now, map_name: 'Nexus', hp: 840, max_hp: 840 };
            debuffsBridge = createDebuffsBridge({ module, playerClass, worldClass, ownedHooks,
                options: { enabled: true, active: true, confuse: effects.includes('confuse'),
                    blind: effects.includes('blind'), silenced: effects.includes('silenced'),
                    quiet: false, record_quiet: effects.includes('quiet') },
                instanceField: (klass, name, type, offset) => ({ offset }),
                resolve: () => ptr(33),
                typedMethod: (klass, name, params, type, isStatic, rva) => ({ native_address: String(0x180000000 + rva) }),
                verifyBytes: (rva, bytes) => ({ rva, bytes }),
                validObject: (object, klass) => object.equals(player) && klass.equals(playerClass) ||
                    object.equals(world) && klass.equals(worldClass),
                isRunning: () => running, getAssociation: () => activeAssociation });
            debuffsBridge.install();
            debuffsBridge.capture(activeAssociation);
            return debuffsBridge.snapshot();
        };
    `, context);
    return { env, ptr, rpc: context.rpc.exports, inspect: context.inspect, markScope: context.markScope,
        captureFailure: context.captureFailure, beginDebuffEpisode: context.beginDebuffEpisode };
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
test('Stop receipts preserve the production Quiet owner interruption after hook cleanup without further target reads', () => {
    const trial = harness(), options = defaults();
    options.autoaim.enabled = true;
    trial.rpc.configure(options);
    assert.equal(trial.beginDebuffEpisode().quiet_observation.last_episode.state, 'active');
    const reads = trial.env.targetReads;
    const receipt = trial.rpc.stop();
    assert.equal(receipt.stopped, true);
    assert.equal(receipt.hooks_detached, 1);
    assert.equal(trial.inspect().hooks, 0);
    assert.equal(trial.env.detached.length, 1);
    assert.equal(trial.env.targetReads, reads);
    assert.equal(receipt.debuffs.quiet_observation.last_episode.state, 'interrupted');
    assert.equal(receipt.debuffs.quiet_observation.last_episode.end_reason, 'stopped');
    assert.equal(receipt.debuffs.quiet_observation.last_episode.after, null);
    assert.equal(receipt.debuffs.quiet_observation.current, null);
    assert.equal(receipt.debuffs.quiet_observation.revision, 2);
    assert.equal(trial.rpc.poll(), null);
});
test('Fallible stop diagnostics cannot prevent resource cleanup or turn it into a resource failure', () => {
    const trial = harness(), options = defaults();
    options.autoaim.enabled = true;
    trial.rpc.configure(options);
    trial.env.failSnapshot = true;
    const receipt = trial.rpc.stop();
    assert.equal(receipt.stopped, true);
    assert.equal(receipt.hooks_detached, 1);
    assert.match(receipt.diagnostics_error, /diagnostic snapshot failed/);
    assert.equal(trial.inspect().hooks, 0);
    assert.equal(trial.env.detached.length, 1);
});
test('Stop receipts preserve both production condition owners after releasing the single Confuse hook', () => {
    const trial = harness(), options = defaults();
    options.autoaim.enabled = true;
    trial.rpc.configure(options);
    const initial = trial.beginDebuffEpisode('both');
    assert.equal(initial.confuse_observation.last_episode.state, 'active');
    assert.equal(initial.quiet_observation.last_episode.state, 'active');
    assert.equal(initial.confuse_observation.current.movement_blocks, 0);
    assert.equal(trial.inspect().hooks, 2); // One simulated aim hook and one production Confuse hook.
    const reads = trial.env.targetReads;
    const receipt = trial.rpc.stop();
    assert.equal(receipt.hooks_detached, 2);
    assert.equal(trial.inspect().hooks, 0);
    assert.equal(trial.env.targetReads, reads);
    for (const effect of ['quiet', 'confuse']) {
        const observation = receipt.debuffs[effect + '_observation'];
        assert.equal(observation.current, null);
        assert.equal(observation.last_episode.state, 'interrupted');
        assert.equal(observation.last_episode.end_reason, 'stopped');
        assert.equal(observation.last_episode.after, null);
        assert.equal(observation.revision, 2);
    }
});
test('Stop receipts retain all three condition interruptions after releasing both visual getters and movement', () => {
    const trial = harness(), options = defaults();
    options.autoaim.enabled = true;
    trial.rpc.configure(options);
    const initial = trial.beginDebuffEpisode('three');
    for (const effect of ['quiet', 'confuse', 'blind']) {
        assert.equal(initial[effect + '_observation'].last_episode.state, 'active', effect);
    }
    assert.equal(initial.blind_observation.current.visual_blocks, 0);
    assert.equal(trial.inspect().hooks, 4); // Aim, Confuse, and two Blind getters.
    const reads = trial.env.targetReads;
    const receipt = trial.rpc.stop();
    assert.equal(receipt.hooks_detached, 4);
    assert.equal(trial.inspect().hooks, 0);
    assert.equal(trial.env.targetReads, reads);
    for (const effect of ['quiet', 'confuse', 'blind']) {
        const observation = receipt.debuffs[effect + '_observation'];
        assert.equal(observation.current, null, effect);
        assert.equal(observation.last_episode.state, 'interrupted', effect);
        assert.equal(observation.last_episode.end_reason, 'stopped', effect);
        assert.equal(observation.last_episode.after, null, effect);
        assert.equal(observation.revision, 2, effect);
    }
});
test('Stop receipts preserve four condition intervals after releasing the experimental local ability gate', () => {
    const trial = harness(), options = defaults();
    options.autoaim.enabled = true;
    trial.rpc.configure(options);
    const initial = trial.beginDebuffEpisode('all');
    for (const effect of ['quiet', 'confuse', 'blind', 'silenced']) {
        assert.equal(initial[effect + '_observation'].last_episode.state, 'active', effect);
    }
    assert.equal(initial.silenced_observation.current.condition_word1, 0x10000);
    assert.equal(initial.silenced_observation.current.ability_gate_bypasses, 0);
    assert.equal(initial.server_acceptance_verified, false);
    assert.equal(trial.inspect().hooks, 5); // Aim, Confuse, two Blind getters, Silenced.
    const reads = trial.env.targetReads;
    const receipt = trial.rpc.stop();
    assert.equal(receipt.hooks_detached, 5);
    assert.equal(trial.inspect().hooks, 0);
    assert.equal(trial.env.targetReads, reads);
    for (const effect of ['quiet', 'confuse', 'blind', 'silenced']) {
        const observation = receipt.debuffs[effect + '_observation'];
        assert.equal(observation.current, null, effect);
        assert.equal(observation.last_episode.state, 'interrupted', effect);
        assert.equal(observation.last_episode.end_reason, 'stopped', effect);
        assert.equal(observation.last_episode.after, null, effect);
        assert.equal(observation.revision, 2, effect);
    }
});
console.log(checks + ' production configuration checks passed (simulated native and child boundaries).');
