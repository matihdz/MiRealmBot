'use strict';

// Independent observational bridge for the host-pinned GameAssembly build.
// This is an auxiliary runtime observer, not REA Evidence. Names below are
// current IL2CPP metadata aliases, not original source/PDB symbols.
// Only owned Interceptor.attach hooks are installed. No managed method is
// invoked, no target field is written, and no input/Nexus action is performed.

let running = false;
let ownedHooks = [];
let latest = null;
let sequence = 0;
let epoch = 0;
let lastIdentity = null;
let startReceipt = null;
let captureError = null;
let activeAssociation = null;
let predictionEnabled = false;
let events = [];
let eventId = 0;
let eventsLost = false;
let scopeGeneration = 0;
const threadScopes = new Map();
const MAX_EVENTS = 256;
const MAX_SCOPE_THREADS = 128;
const MAX_SCOPE_DEPTH = 64;
let diagnostics = {};

function clearScopes() {
    scopeGeneration += 1;
    threadScopes.clear();
}

function rememberCaptureError(error) {
    if (captureError === null) captureError = String(error.stack || error);
}

function detachOwnedHooks() {
    const failed = [];
    const errors = [];
    let detached = 0;
    for (const listener of ownedHooks) {
        try { listener.detach(); detached += 1; }
        catch (error) { failed.push(listener); errors.push(String(error)); }
    }
    ownedHooks = failed;
    if (errors.length !== 0) throw new Error('Could not detach owned hooks: ' + errors.join('; '));
    return detached;
}

function invalidateLifecycle() {
    epoch += 1;
    latest = null;
    lastIdentity = null;
    activeAssociation = null;
    clearScopes();
}

function stop() {
    running = false;
    latest = null;
    activeAssociation = null;
    events = [];
    clearScopes();
    return { stopped: true, hooks_detached: detachOwnedHooks() };
}

function start(options = {}) {
    if (options === null || typeof options !== 'object' || Array.isArray(options) ||
        Object.keys(options).some(key => key !== 'prediction_enabled') ||
        ('prediction_enabled' in options && typeof options.prediction_enabled !== 'boolean')) {
        throw new Error('start options must contain only a boolean prediction_enabled.');
    }
    const requestedPrediction = options.prediction_enabled === true;
    if (running) {
        if (requestedPrediction !== predictionEnabled) throw new Error('Stop the bridge before changing prediction mode.');
        return startReceipt;
    }
    if (ownedHooks.length !== 0) throw new Error('A preceding hook cleanup remains incomplete.');
    if (Process.platform !== 'windows' || Process.arch !== 'x64') {
        throw new Error('This bridge requires Windows x64.');
    }
    const module = Process.getModuleByName('GameAssembly.dll');
    const rx = module.enumerateRanges('r-x');
    const api = {};
    const signatures = {
        domain_get: ['pointer', []], thread_current: ['pointer', []],
        thread_attach: ['pointer', ['pointer']], thread_detach: ['void', ['pointer']],
        class_for_each: ['void', ['pointer', 'pointer']],
        class_get_image: ['pointer', ['pointer']], image_get_name: ['pointer', ['pointer']],
        class_from_name: ['pointer', ['pointer', 'pointer', 'pointer']],
        class_get_name: ['pointer', ['pointer']], class_get_namespace: ['pointer', ['pointer']],
        class_get_parent: ['pointer', ['pointer']], class_get_fields: ['pointer', ['pointer', 'pointer']],
        class_get_method_from_name: ['pointer', ['pointer', 'pointer', 'int']],
        class_get_methods: ['pointer', ['pointer', 'pointer']],
        method_get_name: ['pointer', ['pointer']], method_get_return_type: ['pointer', ['pointer']],
        method_get_param_count: ['uint32', ['pointer']], method_get_param: ['pointer', ['pointer', 'uint32']],
        method_get_flags: ['uint32', ['pointer', 'pointer']],
        field_get_name: ['pointer', ['pointer']], field_get_type: ['pointer', ['pointer']],
        field_get_flags: ['int', ['pointer']], field_get_offset: ['size_t', ['pointer']],
        type_get_name: ['pointer', ['pointer']], free: ['void', ['pointer']],
        object_get_class: ['pointer', ['pointer']],
        string_length: ['int32', ['pointer']], string_chars: ['pointer', ['pointer']]
    };
    for (const [name, [ret, args]] of Object.entries(signatures)) {
        api[name] = new NativeFunction(module.getExportByName('il2cpp_' + name), ret, args,
            { abi: 'win64' });
    }
    const text = address => address.isNull() ? null : address.readUtf8String();
    const number = value => typeof value === 'number' ? value : value.toNumber();
    function className(klass) {
        const namespace = text(api.class_get_namespace(klass));
        const name = text(api.class_get_name(klass));
        return namespace ? namespace + '.' + name : name;
    }
    function typeName(type) {
        const allocated = api.type_get_name(type);
        try { return text(allocated); }
        finally { if (!allocated.isNull()) api.free(allocated); }
    }
    function instanceField(klass, name, expectedType, expectedOffset) {
        const iterator = Memory.alloc(Process.pointerSize);
        for (let current = klass; !current.isNull(); current = api.class_get_parent(current)) {
            iterator.writePointer(ptr(0));
            while (true) {
                const address = api.class_get_fields(current, iterator);
                if (address.isNull()) break;
                if (text(api.field_get_name(address)) !== name) continue;
                const result = {
                    owner: className(current), name,
                    type: typeName(api.field_get_type(address)),
                    flags: api.field_get_flags(address) >>> 0,
                    offset: number(api.field_get_offset(address)),
                    metadata_address: address.toString()
                };
                if ((result.flags & 0x10) !== 0 || result.type !== expectedType || result.offset !== expectedOffset) {
                    throw new Error('Unexpected metadata binding: ' + JSON.stringify(result));
                }
                return result;
            }
        }
        throw new Error('Missing instance field: ' + className(klass) + '.' + name);
    }
    function method(klass, name, expectedRva) {
        const info = api.class_get_method_from_name(klass, Memory.allocUtf8String(name), 0);
        if (info.isNull()) throw new Error('Missing zero-parameter method: ' + name);
        const address = info.readPointer();
        const rva = address.sub(module.base);
        if (!rva.equals(ptr(expectedRva)) || !rx.some(range =>
            address.compare(range.base) >= 0 && address.compare(range.base.add(range.size)) < 0)) {
            throw new Error(name + ' does not match expected executable RVA ' + expectedRva);
        }
        return { owner: className(klass), name, parameter_count: 0,
            metadata_address: info.toString(), native_address: address.toString(),
            native_rva: expectedRva, rx_validated: true };
    }
    function typedMethod(klass, name, parameterTypes, returnType, isStatic, expectedRva) {
        const iterator = Memory.alloc(Process.pointerSize);
        iterator.writePointer(ptr(0));
        const candidates = [];
        while (true) {
            const info = api.class_get_methods(klass, iterator);
            if (info.isNull()) break;
            if (text(api.method_get_name(info)) !== name || api.method_get_param_count(info) !== parameterTypes.length) continue;
            candidates.push(info);
        }
        if (candidates.length !== 1) throw new Error('Expected one declared method binding: ' + className(klass) + '.' + name);
        const info = candidates[0];
        const parameters = parameterTypes.map((_, index) => typeName(api.method_get_param(info, index)));
        const actualReturn = typeName(api.method_get_return_type(info));
        const implementationFlags = Memory.alloc(4);
        implementationFlags.writeU32(0);
        const flags = api.method_get_flags(info, implementationFlags) >>> 0;
        const address = info.readPointer();
        if (JSON.stringify(parameters) !== JSON.stringify(parameterTypes) || actualReturn !== returnType ||
            ((flags & 0x10) !== 0) !== isStatic || !address.equals(module.base.add(expectedRva)) ||
            !rx.some(range => address.compare(range.base) >= 0 && address.compare(range.base.add(range.size)) < 0)) {
            throw new Error('Typed method does not match this profile: ' + className(klass) + '.' + name);
        }
        return { owner: className(klass), name, parameter_count: parameters.length, parameters,
            return_type: actualReturn, is_static: isStatic, flags,
            metadata_address: info.toString(), native_address: address.toString(),
            native_rva: '0x' + expectedRva.toString(16), rx_validated: true };
    }
    function verifyBytes(rva, expected) {
        const address = module.base.add(rva);
        if (!rx.some(range => address.compare(range.base) >= 0 && address.add(expected.length / 2).compare(range.base.add(range.size)) <= 0)) {
            throw new Error('Code guard is outside executable memory: 0x' + rva.toString(16));
        }
        const observed = Array.from(new Uint8Array(address.readByteArray(expected.length / 2)))
            .map(value => value.toString(16).padStart(2, '0')).join('');
        if (observed !== expected) throw new Error('Code bytes do not match this profile at RVA 0x' + rva.toString(16));
        return { native_rva: '0x' + rva.toString(16), native_address: address.toString(), bytes_hex: observed };
    }
    function validObject(object, expectedClass) {
        if (object.isNull()) return false;
        const header = object.readPointer();
        if (!header.equals(api.object_get_class(object))) return false;
        for (let current = header; !current.isNull(); current = api.class_get_parent(current)) {
            if (current.equals(expectedClass)) return true;
        }
        return false;
    }

    const domain = api.domain_get();
    if (domain.isNull()) throw new Error('IL2CPP domain is not initialized.');
    let ownThread = ptr(0);
    let guiClass, playerClass, worldClass, stringClass, baseEntityClass, rootEntityClass, projectileClass;
    let playerField, worldField, worldPlayer, mapName, currentHealth, maximumHealth;
    let update, clean, init;
    let collision, effectiveDamage, baseDamage, healthSync;
    const bindings = {
        schema: 'mirealmbot.internal-health-bridge.v1',
        authority: 'auxiliary runtime observation; not REA Evidence',
        expected_gameassembly_sha256: 'c4f87cde09d713be23c96a5c17774bdbd65e36cd4a4c6fb017759b24daea01cb',
        digest_verification: 'The host verifies the selected file before attaching this script.',
        module: { name: module.name, path: module.path, base: module.base.toString(), size: module.size },
        metadata_thread: { own_thread_attached: false, own_thread_detached: false },
        sample_interval_ms: 50,
        health_source: 'Typed int32 local-player fields: ABCPKBGJPEP current HP and OADOHPKBPJB maximum HP.',
        health_basis: {
            current_hp_rea_evidence: 'ev_ad5a06a1fca9cd33330c155c3d3b9ad924e8df4d7cc6577299f335edec1578480',
            maximum_hp_observation: 'health-probe-20261024T225821.753194Z-pid42848.json: getter RVA 0x77a120, mov eax,[rcx+0x208]; ret'
        },
        health_limitations: requestedPrediction ?
            'Prediction accepts only the verified local-player projectile collision route. Real combat hits remain unverified; ground damage, damage over time and trajectory prediction are not covered.' :
            'Live effective-damage prediction is disabled; tracked HP synchronizes to observed player HP.',
        tracked_health_source: null,
        player_gates: { gate_a: null, gate_b: null,
            reason: 'Condition-mask and object-property bindings are pending; missing gates are never assumed false.' },
        map_identity: {
            components: ['lifecycle_epoch', 'world_pointer', 'player_pointer', 'map_name'],
            lifecycle_hooks: ['GuiManager.Clean', 'GuiManager.Init'],
            limitation: 'Nexus to Vault and Vault to Nexus transitions were observed. Other lifecycle paths have not been exhaustively verified.'
        },
        restrictions: { managed_invocations: false, target_field_writes: false,
            input: false, nexus_action: false, global_detach: false }
    };
    bindings.prediction = { enabled: requestedPrediction, source: requestedPrediction ?
        'Final int32 damage observed at the native base damage handler after game modifiers, scoped to an accepted local-player projectile collision.' : null,
        accepted_causes: requestedPrediction ? ['projectile'] : [],
        real_hit_verified: false, trajectory_prediction: false, ground_damage: false, damage_over_time: false,
        event_capacity: MAX_EVENTS, queue_overflow: 'events_lost remains true until restart; the host must stop before acting.',
        association_max_age_ms: 250, condition_gates: 'unknown',
        condition_array: null };
    try {
        if (api.thread_current().isNull()) {
            ownThread = api.thread_attach(domain);
            if (ownThread.isNull()) throw new Error('Could not attach the metadata observer thread.');
            bindings.metadata_thread.own_thread_attached = true;
        }
        const images = new Map();
        const visitor = new NativeCallback((klass, context) => {
            const image = api.class_get_image(klass);
            const name = text(api.image_get_name(image));
            if (!images.has(name)) images.set(name, image);
        }, 'void', ['pointer', 'pointer'], 'win64');
        api.class_for_each(visitor, ptr(0));
        function resolve(imageName, namespace, name) {
            const image = images.get(imageName);
            if (image === undefined) throw new Error('Image is not loaded: ' + imageName);
            const klass = api.class_from_name(image, Memory.allocUtf8String(namespace), Memory.allocUtf8String(name));
            if (klass.isNull()) throw new Error('Missing class: ' + namespace + '.' + name);
            return klass;
        }
        guiClass = resolve('Assembly-CSharp.dll', 'DecaGames.RotMG.Managers.GUI', 'GuiManager');
        playerClass = resolve('Assembly-CSharp.dll', '', 'FKALGHJIADI');
        worldClass = resolve('Assembly-CSharp.dll', '', 'HJMBOMEHGDJ');
        stringClass = resolve('mscorlib.dll', 'System', 'String');
        playerField = instanceField(guiClass, 'JLFBFCOFDOF', 'FKALGHJIADI', 512);
        worldField = instanceField(guiClass, 'EFAGNJBDCOM', 'HJMBOMEHGDJ', 520);
        worldPlayer = instanceField(worldClass, 'OCLNLBHDEFK', 'FKALGHJIADI', 72);
        mapName = instanceField(worldClass, 'PICNEHLAODO', 'System.String', 280);
        currentHealth = instanceField(playerClass, 'ABCPKBGJPEP', 'System.Int32', 524);
        maximumHealth = instanceField(playerClass, 'OADOHPKBPJB', 'System.Int32', 520);
        const maximumGetter = playerClass.add(0x678).readPointer();
        if (!maximumGetter.equals(module.base.add(0x77a120)) ||
            Array.from(new Uint8Array(maximumGetter.readByteArray(7)))
                .map(value => value.toString(16).padStart(2, '0')).join('') !== '8b8108020000c3') {
            throw new Error('The observed maximum-HP getter does not match this profile.');
        }
        update = method(guiClass, 'Update', '0x3bb080');
        clean = method(guiClass, 'Clean', '0x3b4000');
        init = method(guiClass, 'Init', '0x3b5ab0');
        bindings.classes = [guiClass, playerClass, worldClass, stringClass]
            .map(klass => ({ name: className(klass), metadata_address: klass.toString() }));
        bindings.fields = { playerField, worldField, worldPlayer, mapName, currentHealth, maximumHealth };
        bindings.methods = { update, clean, init };
        if (requestedPrediction) {
            baseEntityClass = resolve('Assembly-CSharp.dll', '', 'LKHPPBEGNOM');
            rootEntityClass = resolve('Assembly-CSharp.dll', '', 'KJMONHENJEN');
            projectileClass = resolve('Assembly-CSharp.dll', '', 'HBEAKBIHANL');
            bindings.classes.push(...[baseEntityClass, rootEntityClass, projectileClass]
                .map(klass => ({ name: className(klass), metadata_address: klass.toString() })));
            bindings.prediction.condition_array = instanceField(baseEntityClass, 'COHCKAPOLCA', 'System.Int32[]', 0x250);
            collision = typedMethod(projectileClass, 'GJFKGLJEGKO', ['System.Int32', 'System.Int32'], 'System.Boolean', false, 0xfefd00);
            effectiveDamage = typedMethod(rootEntityClass, 'LPEDCDIDDLM',
                ['System.Int32', 'System.Int32', 'System.Boolean', 'System.Int32[]', 'LKHPPBEGNOM', 'AMKGEOMDELF'],
                'System.Int32', true, 0x4f79c0);
            baseDamage = typedMethod(baseEntityClass, 'FNEBPDEDIJP',
                ['System.Boolean', 'System.Int32', 'System.Collections.Generic.List<System.Byte>', 'System.Boolean', 'HBEAKBIHANL', 'System.Boolean', 'System.Boolean'],
                'System.Void', false, 0x775710);
            healthSync = typedMethod(baseEntityClass, 'NIDPCHCNDLJ', ['System.Int32'], 'System.Void', false, 0x77a2c0);
            Object.assign(bindings.methods, { collision, effectiveDamage, baseDamage, healthSync });
            bindings.prediction.code_guards = [
                verifyBytes(0xfefd00, '40535741544881ecf0000000803d4d32'),
                verifyBytes(0x4f79c0, '405355565741554883ec30803d3753e5'),
                verifyBytes(0x775710, '488bc444894018885010488948085356'),
                verifyBytes(0x77a2c0, '89910c020000488b11488b8218050000'),
                verifyBytes(0x775ea8, 'f3440f2ce8'),
                verifyBytes(0x775ead, '803d3785bd03007513488d0d2b09a603'),
                verifyBytes(0xff02f6, 'e8c57650ff')
            ];
            bindings.prediction.rea_evidence = [
                'ev_0b3f48464ccffff357701f299368fb6e3141d644168eecef130ecb286c32ba7e6'
            ];
            bindings.tracked_health_source = 'Host consumes ordered final projectile damage and authoritative HP synchronization observations.';
        }
    } finally {
        if (!ownThread.isNull()) {
            api.thread_detach(ownThread);
            bindings.metadata_thread.own_thread_detached = true;
        }
    }

    function managedString(object, maxLength) {
        if (!validObject(object, stringClass)) return null;
        const length = api.string_length(object);
        if (length < 1 || length > maxLength) return null;
        return api.string_chars(object).readUtf16String(length);
    }
    let lastSample = -Infinity;
    function readGui(gui) {
        if (!validObject(gui, guiClass)) return null;
        const player = gui.add(playerField.offset).readPointer();
        const world = gui.add(worldField.offset).readPointer();
        if (!validObject(player, playerClass) || !validObject(world, worldClass) ||
            !player.readPointer().equals(playerClass) ||
            !world.add(worldPlayer.offset).readPointer().equals(player)) {
            return null;
        }
        const name = managedString(world.add(mapName.offset).readPointer(), 128);
        const hp = player.add(currentHealth.offset).readS32();
        const rawMaximum = player.add(maximumHealth.offset).readS32();
        return { gui, player, world, name, hp, maximum: rawMaximum > 0 ? rawMaximum : null,
            identity: name === null ? null : JSON.stringify([world.toString(), player.toString(), name]) };
    }
    function healthFrame(observation, mapId) {
        return { map_id: mapId, map_name: observation.name,
            player_hp: observation.hp, max_hp: observation.maximum,
            gate_a: null, gate_b: null, tracked_hp: null };
    }
    function publish(frame, now) {
        sequence += 1;
        latest = { captured_at: now, frame: Object.assign({ sequence }, frame) };
    }
    function capture(gui, now) {
        const observed = readGui(gui);
        if (observed === null) { latest = null; activeAssociation = null; clearScopes(); return; }
        let mapId = null;
        if (observed.identity !== null) {
            if (lastIdentity !== null && observed.identity !== lastIdentity) { epoch += 1; clearScopes(); }
            lastIdentity = observed.identity;
            mapId = JSON.stringify([epoch, observed.world.toString(), observed.player.toString(), observed.name]);
        }
        activeAssociation = mapId === null ? null : { gui, player: observed.player, world: observed.world,
            identity: observed.identity, map_id: mapId, gui_seen_at: now };
        publish(healthFrame(observed, mapId), now);
    }
    function readEventFrame(now, expectedPlayer = null) {
        const association = activeAssociation;
        if (association === null || now - association.gui_seen_at < 0 || now - association.gui_seen_at > 250 ||
            (expectedPlayer !== null && !association.player.equals(expectedPlayer))) return null;
        const observed = readGui(association.gui);
        if (observed === null || observed.identity !== association.identity ||
            !observed.player.equals(association.player) || !observed.world.equals(association.world)) {
            invalidateLifecycle();
            return null;
        }
        if (observed.maximum === null) return null;
        return healthFrame(observed, association.map_id);
    }
    function appendEvent(kind, frame, now, amount = null) {
        if (events.length >= MAX_EVENTS) {
            eventsLost = true;
            diagnostics.queue_overflows += 1;
            publish(frame, now);
            return;
        }
        eventId += 1;
        const event = { event_id: eventId, kind, captured_at: now, frame };
        if (kind === 'damage') { event.amount = amount; event.cause = 'projectile'; }
        events.push(event);
        diagnostics[kind === 'damage' ? 'final_damage_events' : 'health_sync_events'] += 1;
        publish(frame, now);
    }
    function usable() { return running && captureError === null; }
    function scopeFor(threadId) {
        return threadScopes.get(threadId) || null;
    }
    function enterCollision(threadId, projectile) {
        let state = scopeFor(threadId);
        if (state === null) {
            if (threadScopes.size >= MAX_SCOPE_THREADS) throw new Error('Projectile observer thread capacity exceeded.');
            state = { collisions: [], calculations: [], damage_calls: [] };
            threadScopes.set(threadId, state);
        }
        if (state.collisions.length >= MAX_SCOPE_DEPTH) throw new Error('Projectile observer nesting capacity exceeded.');
        const scope = { generation: scopeGeneration, thread_id: threadId, state, projectile,
            calculation: null, emitted: false };
        state.collisions.push(scope);
        return scope;
    }
    function scopeIsCurrent(scope) {
        return scope !== null && scope.generation === scopeGeneration && scopeFor(scope.thread_id) === scope.state &&
            scope.state.collisions[scope.state.collisions.length - 1] === scope;
    }
    function leaveStack(scope, stackName, token) {
        if (scope.generation !== scopeGeneration) return;
        const state = scopeFor(scope.thread_id);
        if (state !== scope.state) return;
        const stack = state[stackName];
        if (stack[stack.length - 1] !== token) throw new Error('Projectile observer scope order changed.');
        stack.pop();
        if (state.collisions.length === 0 && state.calculations.length === 0 && state.damage_calls.length === 0) {
            threadScopes.delete(scope.thread_id);
        }
    }
    function installPredictionHooks() {
        ownedHooks.push(Interceptor.attach(ptr(collision.native_address), {
            onEnter(args) {
                this.observedScope = null;
                if (!usable()) return;
                try {
                    diagnostics.collision_calls += 1;
                    const projectile = args[0];
                    if (!validObject(projectile, projectileClass)) return;
                    this.observedScope = enterCollision(this.threadId, projectile);
                } catch (error) { rememberCaptureError(error); }
            },
            onLeave(returnValue) {
                if (this.observedScope === null) return;
                try { leaveStack(this.observedScope, 'collisions', this.observedScope); }
                catch (error) { rememberCaptureError(error); }
            }
        }));
        ownedHooks.push(Interceptor.attach(ptr(effectiveDamage.native_address), {
            onEnter(args) {
                this.observedCalculation = null;
                if (!usable()) return;
                try {
                    const state = scopeFor(this.threadId);
                    if (state === null || state.collisions.length === 0 || !this.returnAddress.equals(module.base.add(0xff02fb))) return;
                    const scope = state.collisions[state.collisions.length - 1];
                    const now = Date.now();
                    const frame = readEventFrame(now, args[4]);
                    if (frame === null || !scopeIsCurrent(scope) || !validObject(args[4], playerClass)) return;
                    if (state.calculations.length >= MAX_SCOPE_DEPTH) throw new Error('Damage calculator nesting capacity exceeded.');
                    const calculation = { scope, target: args[4], frame, raw_damage: args[0].toInt32(),
                        defense: args[1].toInt32(), returned: null, complete: false, consumed: false };
                    state.calculations.push(calculation);
                    this.observedCalculation = calculation;
                    diagnostics.local_calculations += 1;
                } catch (error) { rememberCaptureError(error); }
            },
            onLeave(returnValue) {
                const calculation = this.observedCalculation;
                if (calculation === null) return;
                try {
                    const scope = calculation.scope;
                    if (scopeIsCurrent(scope)) {
                        calculation.returned = returnValue.toInt32();
                        calculation.complete = true;
                        scope.calculation = calculation;
                    }
                    leaveStack(scope, 'calculations', calculation);
                } catch (error) { rememberCaptureError(error); }
            }
        }));
        ownedHooks.push(Interceptor.attach(ptr(baseDamage.native_address), {
            onEnter(args) {
                this.observedDamageCall = null;
                if (!usable()) return;
                try {
                    const state = scopeFor(this.threadId);
                    if (state === null || state.collisions.length === 0) return;
                    const scope = state.collisions[state.collisions.length - 1];
                    const calculation = scope.calculation;
                    if (!scopeIsCurrent(scope) || calculation === null || !calculation.complete || calculation.consumed ||
                        scope.emitted || calculation.returned < 0 || !calculation.target.equals(args[0]) ||
                        !scope.projectile.equals(args[5]) || readEventFrame(Date.now(), args[0]) === null) return;
                    if (state.damage_calls.length >= MAX_SCOPE_DEPTH) throw new Error('Damage handler nesting capacity exceeded.');
                    const call = { scope, calculation, target: args[0], projectile: args[5], emitted: false };
                    calculation.consumed = true;
                    state.damage_calls.push(call);
                    this.observedDamageCall = call;
                    diagnostics.matched_damage_entries += 1;
                } catch (error) { rememberCaptureError(error); }
            },
            onLeave(returnValue) {
                const call = this.observedDamageCall;
                if (call === null) return;
                try { leaveStack(call.scope, 'damage_calls', call); }
                catch (error) { rememberCaptureError(error); }
            }
        }));
        ownedHooks.push(Interceptor.attach(module.base.add(0x775ead), {
            onEnter(args) {
                if (!usable()) return;
                try {
                    const state = scopeFor(this.threadId);
                    if (state === null || state.damage_calls.length === 0) return;
                    const call = state.damage_calls[state.damage_calls.length - 1];
                    const scope = call.scope;
                    if (!scopeIsCurrent(scope) || call.emitted || scope.emitted ||
                        !this.context.r12.equals(call.target) || !scope.projectile.equals(call.projectile)) return;
                    const now = Date.now();
                    const frame = readEventFrame(now, call.target);
                    if (frame === null || !scopeIsCurrent(scope) || frame.map_id !== call.calculation.frame.map_id) return;
                    const amount = this.context.r13.toInt32();
                    if (amount < 0) throw new Error('Final projectile damage is negative; this route is not usable.');
                    call.emitted = true;
                    scope.emitted = true;
                    if (amount > 0) appendEvent('damage', frame, now, amount);
                    else diagnostics.zero_damage_hits += 1;
                } catch (error) { rememberCaptureError(error); }
            }
        }));
        ownedHooks.push(Interceptor.attach(ptr(healthSync.native_address), {
            onEnter(args) {
                this.observedHealthSync = null;
                if (!usable()) return;
                try {
                    const now = Date.now();
                    const frame = readEventFrame(now, args[0]);
                    if (frame !== null && validObject(args[0], playerClass)) {
                        this.observedHealthSync = { target: args[0], generation: scopeGeneration, map_id: frame.map_id };
                    }
                } catch (error) { rememberCaptureError(error); }
            },
            onLeave(returnValue) {
                const sync = this.observedHealthSync;
                if (!usable() || sync === null || sync.generation !== scopeGeneration) return;
                try {
                    const now = Date.now();
                    const frame = readEventFrame(now, sync.target);
                    if (frame !== null && frame.map_id === sync.map_id) appendEvent('health_sync', frame, now);
                } catch (error) { rememberCaptureError(error); }
            }
        }));
    }
    try {
        ownedHooks.push(Interceptor.attach(ptr(clean.native_address), {
            onEnter(args) { if (running) invalidateLifecycle(); }
        }));
        ownedHooks.push(Interceptor.attach(ptr(init.native_address), {
            onEnter(args) { if (running) invalidateLifecycle(); }
        }));
        ownedHooks.push(Interceptor.attach(ptr(update.native_address), {
            onEnter(args) { this.gui = args[0]; },
            onLeave(returnValue) {
                if (!running) return;
                const now = Date.now();
                if (now - lastSample < 50) return;
                lastSample = now;
                try { capture(this.gui, now); }
                catch (error) { latest = null; rememberCaptureError(error); }
            }
        }));
        if (requestedPrediction) installPredictionHooks();
        latest = null;
        lastIdentity = null;
        activeAssociation = null;
        sequence = 0;
        epoch = 0;
        events = [];
        eventId = 0;
        eventsLost = false;
        clearScopes();
        captureError = null;
        diagnostics = { collision_calls: 0, local_calculations: 0, matched_damage_entries: 0,
            final_damage_events: 0, health_sync_events: 0, zero_damage_hits: 0, queue_overflows: 0 };
        predictionEnabled = requestedPrediction;
        startReceipt = { pid: Process.id, module_path: module.path, bindings };
        running = true;
        return startReceipt;
    } catch (error) {
        running = false;
        latest = null;
        activeAssociation = null;
        events = [];
        clearScopes();
        try { detachOwnedHooks(); }
        catch (cleanupError) { throw new Error(String(error) + '; cleanup: ' + String(cleanupError)); }
        throw error;
    }
}

rpc.exports = {
    start,
    poll() {
        if (running && captureError !== null) throw new Error('Runtime capture failed: ' + captureError);
        if (!running || latest === null) return null;
        const now = Date.now();
        const drained = events.map(event => {
            const result = Object.assign({}, event, { age_ms: now - event.captured_at });
            delete result.captured_at;
            return result;
        });
        events = [];
        return Object.assign({}, latest.frame, { age_ms: now - latest.captured_at,
            events: drained, events_lost: eventsLost,
            diagnostics: Object.assign({}, diagnostics, { active_scope_threads: threadScopes.size }) });
    },
    stop
};
