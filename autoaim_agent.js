'use strict';

// Only Close to Mouse is supported. Extrapolation is the original numerical
// coefficient: predicted position = position + coefficient * movement vector.
// Its time unit has not been established; it is deliberately not labelled ms.
function chooseAutoAimTarget(candidates, cursor, radius, extrapolation, prioritizeBosses = false, ignoreWalls = false) {
    if (![cursor.x, cursor.y, radius, extrapolation].every(Number.isFinite) ||
        radius <= 0 || extrapolation < 0 || typeof prioritizeBosses !== 'boolean' ||
        typeof ignoreWalls !== 'boolean') return null;
    let chosen = null;
    const radiusSquared = radius * radius;
    let best = radiusSquared;
    let bestPriority = -1;
    for (const candidate of candidates) {
        if (!candidate.eligible || ![candidate.x, candidate.y].every(Number.isFinite)) continue;
        if (ignoreWalls && candidate.is_wall === true) continue;
        const dx = candidate.x - cursor.x;
        const dy = candidate.y - cursor.y;
        const distance = dx * dx + dy * dy;
        const priority = prioritizeBosses && candidate.is_boss === true ? 1 : 0;
        if (!(distance < radiusSquared) || priority < bestPriority ||
            (priority === bestPriority && !(distance < best))) continue;
        const vx = extrapolation === 0 ? 0 : candidate.vx;
        const vy = extrapolation === 0 ? 0 : candidate.vy;
        if (![vx, vy].every(Number.isFinite)) continue;
        const factor = Math.fround(extrapolation);
        const x = Math.fround(candidate.x + Math.fround(factor * vx));
        const y = Math.fround(candidate.y + Math.fround(factor * vy));
        if (![x, y].every(Number.isFinite)) continue;
        best = distance;
        bestPriority = priority;
        chosen = { id: candidate.id, x, y, source_x: candidate.x, source_y: candidate.y,
            is_boss: typeof candidate.is_boss === 'boolean' ? candidate.is_boss : null,
            is_wall: typeof candidate.is_wall === 'boolean' ? candidate.is_wall : null,
            mouse_distance: Math.sqrt(distance) };
    }
    return chosen;
}

function createAutoAimBridge(context) {
    const { module, api, resolve, instanceField, typedMethod, verifyBytes,
        validObject, ownedHooks, options, isRunning, getAssociation, playerClass, worldClass } = context;
    if (options.mode !== 'close_to_mouse' || options.activation !== 'autofire' ||
        typeof options.enabled !== 'boolean' || typeof options.active !== 'boolean' ||
        typeof options.prioritize_bosses !== 'boolean' ||
        typeof options.ignore_walls !== 'boolean' ||
        !Number.isFinite(options.extrapolation) || options.extrapolation < 0 ||
        !Number.isFinite(options.mouse_radius) || options.mouse_radius <= 0) {
        throw Error('Invalid Close to Mouse options.');
    }
    const inputClass = resolve('Assembly-CSharp.dll', 'DecaGames.RotMG.Managers.Options', 'InputManager');
    const cameraManagerClass = resolve('Assembly-CSharp.dll', 'DecaGames.RotMG.Managers', 'CameraManager');
    const cameraClass = resolve('UnityEngine.CoreModule.dll', 'UnityEngine', 'Camera');
    const entityClass = resolve('Assembly-CSharp.dll', '', 'KJMONHENJEN');
    const livingClass = resolve('Assembly-CSharp.dll', '', 'LKHPPBEGNOM');
    const propertiesClass = resolve('Assembly-CSharp.dll', 'DecaGames.RotMG.Objects.Map.Data', 'ObjectProperties');
    const bossMarkerClass = resolve('Assembly-CSharp.dll', 'DecaGames.RotMG.Objects.Map.Data', 'HealthBarBoss');
    const entityKindClass = resolve('Assembly-CSharp.dll', '', 'FDCIMDHOOCB');
    const fields = {
        playerInput: instanceField(playerClass, 'BOGNJLHEBGL', 'DecaGames.RotMG.Managers.Options.InputManager', 0x620),
        inputWorld: instanceField(inputClass, 'EFAGNJBDCOM', 'HJMBOMEHGDJ', 0x28),
        inputCameraManager: instanceField(inputClass, 'LBHJKFJFDHD', 'DecaGames.RotMG.Managers.CameraManager', 0x30),
        autoFire: instanceField(inputClass, 'BINDBHJLPMG', 'System.Boolean', 0x64),
        pausedAutoFire: instanceField(inputClass, '<MOEJAIJAALE>k__BackingField', 'System.Boolean', 0xa8),
        camera: instanceField(cameraManagerClass, 'KNAIAEFDCLM', 'UnityEngine.Camera', 0x50),
        entities: instanceField(worldClass, 'DFALIKKKGLI', 'System.Collections.Generic.Dictionary<System.Int32,LKHPPBEGNOM>', 0xc0),
        x: instanceField(entityClass, 'CLFEOFKBNEJ', 'System.Single', 0x3c),
        y: instanceField(entityClass, 'PKEECFNFEIO', 'System.Single', 0x40),
        id: instanceField(entityClass, 'FAEMCAEIPMP', 'System.Int32', 0x4c),
        kind: instanceField(entityClass, 'JIPGLAPHDCL', 'FDCIMDHOOCB', 0xbc),
        properties: instanceField(entityClass, 'OBAKMCCDBJA', 'DecaGames.RotMG.Objects.Map.Data.ObjectProperties', 0x18),
        hostile: instanceField(propertiesClass, 'isEnemy', 'System.Boolean', 0x6e1),
        bossMarker: instanceField(propertiesClass, 'HealthBarBossElement',
            'DecaGames.RotMG.Objects.Map.Data.HealthBarBoss', 0x488),
        damageable: instanceField(propertiesClass, 'MaxHitPointsElement', 'DecaGames.RotMG.Objects.Map.Data.MaxHitPoints', 0x368),
        health: instanceField(livingClass, 'ABCPKBGJPEP', 'System.Int32', 0x20c),
        conditions: instanceField(livingClass, 'COHCKAPOLCA', 'System.Int32[]', 0x250),
        velocity: instanceField(livingClass, 'ECGPFJKCCAN', 'UnityEngine.Vector2', 0x278)
    };
    const dispatch = typedMethod(inputClass, 'NBGCFFLKGHD', [], 'System.Void', false, 0xe94580);
    const camera = typedMethod(cameraClass, 'ScreenToWorldPoint', ['UnityEngine.Vector3'],
        'UnityEngine.Vector3', false, 0x33d0930);
    const guards = [verifyBytes(0xe94580, '48895c2408574883ec60488b4130488bd9'),
        verifyBytes(0xe945de, 'e84dc35302'), verifyBytes(0xe945e3, 'f20f1000f20f11442420'),
        verifyBytes(0xe91ae0, '0fb64164c3'), verifyBytes(0xe8dc70, '0fb681a8000000c3'),
        verifyBytes(0x33d0930, '48895c2410564883ec40f2410f100033c0')];
    const elementClass = new NativeFunction(module.getExportByName('il2cpp_class_get_element_class'),
        'pointer', ['pointer'], { abi: 'win64' });
    const valueSize = new NativeFunction(module.getExportByName('il2cpp_class_value_size'),
        'int32', ['pointer', 'pointer'], { abi: 'win64' });
    const readEnumLiteral = new NativeFunction(module.getExportByName('il2cpp_field_static_get_value'),
        'void', ['pointer', 'pointer'], { abi: 'win64' });
    const wallClasses = { CaveWall: 5, ConnectedWall: 11, Wall: 34, DoubleWall: 36 };
    const wallKinds = new Set();
    const knownKinds = new Set();
    const kindIterator = Memory.alloc(Process.pointerSize); kindIterator.writePointer(ptr(0));
    const kindValue = Memory.alloc(4);
    for (;;) {
        const field = api.class_get_fields(entityKindClass, kindIterator);
        if (field.isNull()) break;
        if ((api.field_get_flags(field) & 0x50) !== 0x50) continue;
        const name = api.field_get_name(field).readUtf8String();
        readEnumLiteral(field, kindValue);
        const value = kindValue.readS32();
        knownKinds.add(value);
        if (Object.prototype.hasOwnProperty.call(wallClasses, name)) {
            if (wallClasses[name] !== value) throw Error('Unsupported wall entity enum: ' + name);
            wallKinds.add(value);
        }
    }
    if (wallKinds.size !== Object.keys(wallClasses).length) throw Error('Missing wall entity enum metadata.');
    const user32 = Process.getModuleByName('user32.dll');
    const foregroundWindow = new NativeFunction(user32.getExportByName('GetForegroundWindow'),
        'pointer', [], { abi: 'win64' });
    const windowProcess = new NativeFunction(user32.getExportByName('GetWindowThreadProcessId'),
        'uint32', ['pointer', 'pointer'], { abi: 'win64' });
    const foregroundPid = Memory.alloc(4);
    const returnAddress = module.base.add(0xe945e3);
    const scopes = new Map();
    const layouts = new Map();
    const MAX_ENTITIES = 16384; // <=384 KiB of entries per native cursor projection.
    let generation = 0;
    let error = null;
    let identity = null;
    let state = { autofire: null, paused: null, foreground: false, target: null, angle: null,
        candidates: 0, projections: 0, redirects: 0, captured_at: null };

    function fail(caught) { if (error === null) error = String(caught.stack || caught); scopes.clear(); }
    function foreground() {
        const hwnd = foregroundWindow();
        if (hwnd.isNull()) return false;
        foregroundPid.writeU32(0); windowProcess(hwnd, foregroundPid);
        return foregroundPid.readU32() === Process.id;
    }
    function current() {
        if (!isRunning() || error !== null) return null;
        const association = getAssociation();
        const age = association === null ? Infinity : Date.now() - association.gui_seen_at;
        if (association === null || age < 0 || age > 250) return null;
        const input = association.player.add(fields.playerInput.offset).readPointer();
        if (!validObject(input, inputClass) ||
            !input.add(fields.inputWorld.offset).readPointer().equals(association.world)) return null;
        return { association, input };
    }
    function capture(association) {
        try {
            const observed = current();
            if (observed === null || association.identity !== observed.association.identity) return;
            if (identity !== association.identity) { invalidate(); identity = association.identity; }
            state.autofire = observed.input.add(fields.autoFire.offset).readU8() !== 0;
            state.paused = observed.input.add(fields.pausedAutoFire.offset).readU8() !== 0;
            state.foreground = foreground();
            if (!state.autofire || state.paused || !state.foreground) {
                state.target = null; state.angle = null; state.candidates = 0;
            }
            state.captured_at = Date.now();
        } catch (caught) { fail(caught); }
    }
    function metadataFields(klass) {
        const iterator = Memory.alloc(Process.pointerSize); iterator.writePointer(ptr(0));
        const result = new Map();
        for (;;) {
            const field = api.class_get_fields(klass, iterator);
            if (field.isNull()) return result;
            if ((api.field_get_flags(field) & 16) !== 0) continue;
            const name = api.field_get_name(field).readUtf8String();
            const rawOffset = api.field_get_offset(field);
            const offset = typeof rawOffset === 'number' ? rawOffset : rawOffset.toNumber();
            result.set(name, offset);
        }
    }
    function dictionaryLayout(dictionary) {
        const klass = api.object_get_class(dictionary);
        const key = klass.toString();
        if (layouts.has(key)) return layouts.get(key);
        const observed = metadataFields(klass);
        // Dictionary names and offsets are checked on the actual instantiated
        // owner. Unsupported layouts stop only AutoAim, without guessing.
        if (observed.get('_entries') !== 0x18 || observed.get('_count') !== 0x20 ||
            !observed.has('_version')) throw Error('Unsupported entity Dictionary metadata layout.');
        const entries = dictionary.add(0x18).readPointer();
        if (entries.isNull()) return null;
        const entryClass = elementClass(api.object_get_class(entries));
        if (entryClass.isNull()) throw Error('Missing entity Dictionary entry metadata.');
        const entryFields = metadataFields(entryClass);
        const base = entryFields.get('hashCode');
        const alignment = Memory.alloc(4);
        if (base !== 16 || entryFields.get('next') !== base + 4 ||
            entryFields.get('key') !== base + 8 || entryFields.get('value') !== base + 16 ||
            valueSize(entryClass, alignment) !== 24) throw Error('Unsupported entity Dictionary entry layout.');
        const layout = { entries: 0x18, count: 0x20, version: observed.get('_version'), stride: 24 };
        layouts.set(key, layout);
        return layout;
    }
    function candidates(world, player) {
        const dictionary = world.add(fields.entities.offset).readPointer();
        if (dictionary.isNull()) return [];
        const layout = dictionaryLayout(dictionary);
        if (layout === null) return [];
        const version = dictionary.add(layout.version).readS32();
        const count = dictionary.add(layout.count).readS32();
        const entries = dictionary.add(layout.entries).readPointer();
        if (entries.isNull() || count === 0) return [];
        const length = entries.add(0x18).readU64().toNumber();
        if (count < 0 || count > length || count > MAX_ENTITIES) throw Error('Entity Dictionary exceeds the supported snapshot capacity.');
        const result = [];
        for (let index = 0; index < count; index++) {
            const entry = entries.add(0x20 + index * layout.stride);
            if (entry.readS32() < 0) continue;
            const entity = entry.add(16).readPointer();
            if (entity.isNull() || entity.equals(player) || !validObject(entity, livingClass)) continue;
            const properties = entity.add(fields.properties.offset).readPointer();
            if (!validObject(properties, propertiesClass) ||
                properties.add(fields.hostile.offset).readU8() === 0 ||
                properties.add(fields.damageable.offset).readPointer().isNull() ||
                entity.add(fields.health.offset).readS32() <= 0) continue;
            const conditions = entity.add(fields.conditions.offset).readPointer();
            if (conditions.isNull() || conditions.add(0x18).readU64().toNumber() < 1) continue;
            const mask = conditions.add(0x20).readU32();
            if ((mask & ((1 << 21) | (1 << 23) | (1 << 24))) !== 0) continue;
            // This is the game's explicit <HealthBarBoss> description marker.
            // An absent marker does not prove an enemy is never called a boss;
            // it means it is outside this classifier's documented marker scope.
            const marker = properties.add(fields.bossMarker.offset).readPointer();
            const isBoss = marker.isNull() ? false : validObject(marker, bossMarkerClass) ? true : null;
            const kind = entity.add(fields.kind.offset).readS32();
            result.push({ eligible: true, id: entity.add(fields.id.offset).readS32(),
                is_boss: isBoss,
                is_wall: knownKinds.has(kind) ? wallKinds.has(kind) : null,
                x: entity.add(fields.x.offset).readFloat(), y: entity.add(fields.y.offset).readFloat(),
                vx: entity.add(fields.velocity.offset).readFloat(),
                vy: entity.add(fields.velocity.offset + 4).readFloat() });
        }
        if (dictionary.add(layout.version).readS32() !== version ||
            !dictionary.add(layout.entries).readPointer().equals(entries)) throw Error('Entity Dictionary changed during the aim snapshot.');
        return result;
    }
    function invalidate() {
        generation += 1; scopes.clear(); identity = null;
        state = { autofire: null, paused: null, foreground: false, target: null, angle: null,
            candidates: 0, projections: 0, redirects: 0, captured_at: null };
    }
    function install() {
        if (!options.enabled) return;
        ownedHooks.push(Interceptor.attach(ptr(dispatch.native_address), {
            onEnter(args) {
                this.aimScope = null;
                try {
                    const observed = current();
                    if (observed === null || !observed.input.equals(args[0]) ||
                        observed.input.add(fields.autoFire.offset).readU8() === 0 ||
                        observed.input.add(fields.pausedAutoFire.offset).readU8() !== 0 || !foreground()) return;
                    // This routine also serves manual shooting. The real AutoFire
                    // field above is therefore required even inside this scope.
                    if (scopes.has(this.threadId)) throw Error('Unexpected nested AutoAim dispatch.');
                    const cameraManager = observed.input.add(fields.inputCameraManager.offset).readPointer();
                    if (!validObject(cameraManager, cameraManagerClass)) return;
                    const targetCamera = cameraManager.add(fields.camera.offset).readPointer();
                    if (!validObject(targetCamera, cameraClass)) return;
                    const scope = { generation, association: observed.association, input: observed.input,
                        camera: targetCamera, thread: this.threadId, consumed: false };
                    scopes.set(this.threadId, scope); this.aimScope = scope;
                } catch (caught) { fail(caught); }
            },
            onLeave() {
                const scope = this.aimScope;
                if (scope !== null && scopes.get(scope.thread) === scope) scopes.delete(scope.thread);
            }
        }));
        ownedHooks.push(Interceptor.attach(ptr(camera.native_address), {
            onEnter(args) {
                this.aimProjection = null;
                if (!this.returnAddress.equals(returnAddress)) return;
                const scope = scopes.get(this.threadId);
                if (scope === undefined || scope.generation !== generation || scope.consumed ||
                    !scope.camera.equals(args[1])) return;
                this.aimProjection = { scope, buffer: args[0] };
            },
            onLeave(result) {
                const projection = this.aimProjection;
                if (projection === null) return;
                try {
                    const scope = projection.scope;
                    const observed = current();
                    if (observed === null || scope.generation !== generation ||
                        scopes.get(this.threadId) !== scope || scope.consumed ||
                        observed.association.identity !== scope.association.identity ||
                        observed.association.map_id !== scope.association.map_id ||
                        !observed.input.equals(scope.input) ||
                        observed.input.add(fields.autoFire.offset).readU8() === 0 ||
                        observed.input.add(fields.pausedAutoFire.offset).readU8() !== 0 || !foreground()) return;
                    scope.consumed = true;
                    if (!result.equals(projection.buffer)) throw Error('Camera return buffer ABI mismatch.');
                    const range = Process.findRangeByAddress(projection.buffer);
                    if (range === null || !range.protection.includes('w') ||
                        projection.buffer.add(12).compare(range.base.add(range.size)) > 0) throw Error('Camera output is not a writable Vector3 buffer.');
                    const cursor = { x: projection.buffer.readFloat(), y: -projection.buffer.add(4).readFloat() };
                    const observedCandidates = candidates(observed.association.world, observed.association.player);
                    const chosen = chooseAutoAimTarget(observedCandidates, cursor,
                        options.mouse_radius, options.extrapolation, options.prioritize_bosses, options.ignore_walls);
                    state.autofire = true; state.paused = false; state.foreground = true;
                    state.target = chosen; state.candidates = observedCandidates.length;
                    state.angle = chosen === null ? null : Math.atan2(
                        chosen.y - observed.association.player.add(fields.y.offset).readFloat(),
                        chosen.x - observed.association.player.add(fields.x.offset).readFloat());
                    state.projections += 1; state.captured_at = Date.now();
                    if (chosen !== null && options.active) {
                        const output = new ArrayBuffer(8); const view = new DataView(output);
                        view.setFloat32(0, chosen.x, true); view.setFloat32(4, -chosen.y, true);
                        // Replace only this call's stack return x/y. Z, the
                        // physical cursor, game fields and firing state stay owned
                        // by the game. The game computes and fires its own angle.
                        projection.buffer.writeByteArray(output);
                        state.redirects += 1;
                    }
                } catch (caught) { fail(caught); }
            }
        }));
    }
    return { install, capture, invalidate,
        bindings: { fields, dispatch, camera, code_guards: guards,
            boss_classification: {
                source: 'health_bar_boss_marker', field: 'ObjectProperties.HealthBarBossElement',
                present: 'Typed HealthBarBoss description from the game is present.',
                absent: 'No boss health-bar marker; this does not establish that the enemy is not a boss.',
                unknown: 'A non-null description could not be verified as HealthBarBoss.',
                excludes_heuristics: ['maximum HP', 'size', 'Quest flag', 'God flag', 'name matching']
            },
            wall_classification: {
                source: 'game_entity_class', field: 'KJMONHENJEN.JIPGLAPHDCL',
                enum_type: 'FDCIMDHOOCB', wall_classes: wallClasses,
                verification: 'Literal enum names and values checked against live metadata before use.',
                unknown: 'An entity kind absent from the observed enum remains unclassified.',
                scope: 'Wall, CaveWall, ConnectedWall and DoubleWall targets only; collision and projectiles are untouched.',
                excludes_heuristics: ['FullOccupy', 'Static', 'BlockProjectiles', 'maximum HP', 'name matching']
            },
            source: 'Native autofire dispatch camera projection; exact local-player and call-site association.',
            extrapolation_unit: 'original coefficient; no time unit asserted',
            writes: options.enabled && options.active ? 'Scoped camera return stack x/y only' : 'none',
            managed_invocations: false, physical_cursor_changes: false,
            static_rea_evidence: ['ev_391a2fd10ec43022b157de8734ad43b492d2aeaa11f2e7180ef4f11abacf18c9',
                'ev_36da23cf9639af72326c403c86c8fefe0c982186cd5a8ea41de6249fcc82fa76'],
            runtime_combat_verified: false, max_entity_entries: MAX_ENTITIES },
        snapshot() {
            const association = getAssociation();
            const age = association === null ? Infinity : Date.now() - association.gui_seen_at;
            const latest = association !== null && association.identity === identity && age >= 0 && age <= 250;
            let status;
            if (!options.enabled) status = 'Desactivado';
            else if (error !== null) status = 'AutoAim detenido: ' + error.split('\n')[0];
            else if (!latest || state.autofire === null) status = 'Esperando datos del jugador';
            else if (!state.autofire) status = 'Esperando que enciendas el autofire del juego';
            else if (state.paused) status = 'Autofire pausado por el juego';
            else if (!state.foreground) status = 'Pausado: vuelve a la ventana del juego';
            else if (state.target === null) status = 'Sin enemigo válido dentro del radio';
            else status = options.active ? 'Apuntando al enemigo junto al cursor' : 'Objetivo detectado; sólo observación';
            return { enabled: options.enabled, active: options.active, mode: options.mode,
                activation: options.activation, extrapolation: options.extrapolation,
                prioritize_bosses: options.prioritize_bosses,
                ignore_walls: options.ignore_walls,
                mouse_radius: options.mouse_radius, error, status,
                autofire: latest ? state.autofire : null, paused: latest ? state.paused : null,
                foreground: latest && state.foreground, target: latest ? state.target : null,
                target_id: latest && state.target !== null ? state.target.id : null,
                target_is_boss: latest && state.target !== null ? state.target.is_boss : null,
                target_is_wall: latest && state.target !== null ? state.target.is_wall : null,
                angle: latest ? state.angle : null,
                candidates: latest ? state.candidates : 0, projections: state.projections,
                redirects: state.redirects,
                age_ms: latest && state.captured_at !== null ? Date.now() - state.captured_at : null };
        }
    };
}
