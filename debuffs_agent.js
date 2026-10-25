'use strict';

// Protection changes only scoped movement/visual/local-ability getter results. Recording reads
// the existing GUI association; it adds no hooks, game calls or field writes.
function createDebuffsBridge(context) {
    const { module, resolve, instanceField, typedMethod, verifyBytes, validObject,
        ownedHooks, isRunning, getAssociation, playerClass, worldClass } = context;
    const quietReason = 'AntiQuiet no disponible: no se ha verificado una ruta que evite su efecto sobre el maná y las habilidades.';
    function validate(next) {
        if (next === null || typeof next !== 'object' || Array.isArray(next) ||
            Object.keys(next).some(key => !['enabled', 'active', 'confuse', 'blind', 'silenced', 'quiet', 'record_quiet'].includes(key)) ||
            ['enabled', 'active', 'confuse', 'quiet'].some(key => typeof next[key] !== 'boolean') ||
            ('blind' in next && typeof next.blind !== 'boolean') ||
            ('silenced' in next && typeof next.silenced !== 'boolean') ||
            ('record_quiet' in next && typeof next.record_quiet !== 'boolean')) {
            throw Error('Debuffs requires explicit enabled, active, confuse and quiet booleans; record_quiet must be boolean.');
        }
        if (next.quiet) throw Error(quietReason);
        return { enabled: next.enabled, active: next.active, confuse: next.confuse,
            blind: next.blind === true, silenced: next.silenced === true,
            quiet: false, record_quiet: next.record_quiet === true };
    }
    let options = validate(context.options);
    const enabledConfuse = () => options.enabled && options.confuse;
    const enabledBlind = () => options.enabled && options.blind;
    const enabledSilenced = () => options.enabled && options.silenced;
    const enabledQuietRecording = () => options.enabled && options.record_quiet;
    const hasProtection = () => enabledConfuse() || enabledBlind() || enabledSilenced();
    const hasActivity = () => hasProtection() || enabledQuietRecording();
    let generation = 0;
    let identity = null;
    let common = null;
    function protectionState(name, label, enabled, prepare, sampleFields) {
        return { name, label, enabled, prepare, sampleFields, gates: null, installed: false, error: null,
            counters: { checks: 0, detected: 0, blocked: 0 } };
    }
    const movement = protectionState('confuse', 'AntiConfuse', enabledConfuse, prepareMovement,
        { checks: 'movement_checks', detected: 'movement_detections', blocked: 'movement_blocks' });
    const visual = protectionState('blind', 'AntiBlind', enabledBlind, prepareVisual,
        { checks: 'visual_checks', detected: 'visual_detections', blocked: 'visual_blocks' });
    const ability = protectionState('silenced', 'AntiSilenced (experimental)', enabledSilenced, prepareAbility,
        { checks: 'ability_gate_checks', detected: 'ability_gate_detections', blocked: 'ability_gate_bypasses' });
    const protections = [movement, visual, ability];
    let conditionField = null;
    let conditionMetadataAttempted = false;
    let conditionMetadataError = null;
    let quietFields = null;
    let quietMetadataAttempted = false;
    let quietMetadataError = null;
    function observationState(name, label, wordIndex, mask, enabled, protection = null) {
        return { name, label, wordIndex, mask, enabled, protection, current: null, identity: null,
            error: null, before: null, last_episode: null, detections: 0, revision: 0 };
    }
    const quietState = observationState('quiet', 'Quiet', 0, 2, enabledQuietRecording);
    const confuseState = observationState('confuse', 'Confuse', 0, 0x400, enabledConfuse, movement);
    const blindState = observationState('blind', 'Blind', 0, 0x80, enabledBlind, visual);
    const silencedState = observationState('silenced', 'Silenced', 1, 0x10000, enabledSilenced, ability);
    const observations = [quietState, confuseState, blindState, silencedState];
    const state = { foreground: false, captured_at: null };
    const bindings = { quiet_supported: false, quiet_reason: quietReason, blind_supported: true,
        silenced_experimental: true, server_acceptance_verified: false,
        source: 'Local-player condition results at verified movement, visual and local ability call sites.',
        writes: hasProtection() && options.active ? 'Scoped boolean return only' : 'none',
        condition_field_writes: false, mana_writes: false, cooldown_writes: false, network_changes: false,
        managed_invocations: false, runtime_combat_verified: false,
        quiet_observation: { quiet_mask: quietState.mask, condition_word_index: quietState.wordIndex, hooks_added: 0, field_writes: false,
            game_method_invocations: false, fields: null,
            authority: 'Auxiliary local field observation; not REA Evidence or server state.',
            static_rea_evidence: [
                'ev_d11b506b9adb55071a7206a433e0eb2e6f0fcc89ce03cb60611de37d82b75cad',
                'ev_4837d6d743a4e61709d95002e6689ea9164c471c06c7539f47a2ec7297e98743' ],
            limitations: 'GUI samples do not establish hit, ability or packet causality; mp_sync is a local integer backing field.' },
        confuse_observation: { confuse_mask: confuseState.mask, condition_word_index: confuseState.wordIndex, hooks_added: 0, field_writes: false,
            game_method_invocations: false, fields: null,
            authority: 'Auxiliary local condition observation; not REA Evidence or proof of movement protection.',
            static_rea_evidence: [
                'ev_cc064b400188f59c5275a09ce99d60ba7e836a099b165c0b25351b5dd6660b38',
                'ev_d11b506b9adb55071a7206a433e0eb2e6f0fcc89ce03cb60611de37d82b75cad' ],
            limitations: 'Observed condition intervals and scoped movement calls are independent measurements; counters in samples are cumulative.' },
        blind_observation: { blind_mask: blindState.mask, condition_word_index: blindState.wordIndex, hooks_added: 0, field_writes: false,
            game_method_invocations: false, fields: null,
            authority: 'Auxiliary local condition observation; not REA Evidence or proof of visual protection.',
            static_rea_evidence: [
                'ev_04ea5086cfe64749515cb8bd63bc5569152b69c63e7a9963d75c7fe253708334',
                'ev_29235b87a05dc1fa78a5882f0b477a4455af32857460fca8f76b242ac02596fd',
                'ev_d11b506b9adb55071a7206a433e0eb2e6f0fcc89ce03cb60611de37d82b75cad' ],
            limitations: 'Observed Blind intervals and scoped visual calls are independent measurements; counters in samples are cumulative.' },
        silenced_observation: { silenced_mask: silencedState.mask, condition_word_index: silencedState.wordIndex,
            hooks_added: 0, field_writes: false, game_method_invocations: false, fields: null,
            experimental: true, server_acceptance_verified: false,
            authority: 'Auxiliary condition and local gate observations; not REA Evidence or ability acceptance.',
            static_rea_evidence: ['ev_66e38a9b46dc3bf9a1be095b7ecdceca6287cb7a21319b109322dc5983dd865f', 'ev_6611cc2b6f2870a405007c5d7cf7a101745a30dfcfcb1320b5daeee3d067dfa0'],
            limitations: 'A local Silenced gate bypass does not count an ability used or accepted; MP changes are not acceptance evidence.' },
        static_rea_evidence: [
            'ev_31b47b04e067dbaa56b94ceb43712103a231ebefcbfe83d0ae7668c5dfcd9bde',
            'ev_3d89fb32874582a47265b781fa5872b85ad4ea61a020230b86bd2fe71a830033',
            'ev_cc064b400188f59c5275a09ce99d60ba7e836a099b165c0b25351b5dd6660b38',
            'ev_9b08e612374910d3171d0c64016dc054914de26927078c5b05f2ada2376aff36'
        ] };
    function interruptObservation(observation, reason) {
        const episode = observation.last_episode;
        if (episode !== null && episode.state === 'active') {
            observation.last_episode = Object.assign({}, episode,
                { state: 'interrupted', end_reason: reason, after: null });
            observation.revision += 1;
        }
        observation.before = null;
    }
    function invalidateObservation(observation, reason) {
        interruptObservation(observation, reason);
        observation.identity = null;
        observation.current = null;
    }
    function invalidate(reason = 'lifecycle_invalidated') {
        generation += 1;
        identity = null;
        state.foreground = false;
        state.captured_at = null;
        for (const observation of observations) invalidateObservation(observation, reason);
    }
    function observationSnapshot(observation) {
        const association = getAssociation(), current = observation.current;
        const age = current === null ? null : Date.now() - current.timestamp_ms;
        const associationAge = association === null ? null : Date.now() - association.gui_seen_at;
        const same = association !== null && current !== null &&
            observation.identity === association.identity && current.map_id === association.map_id;
        const enabled = observation.enabled();
        const fresh = enabled && isRunning() && same && age >= 0 && age <= 250 &&
            associationAge >= 0 && associationAge <= 250;
        const observationError = conditionMetadataError ||
            (observation === quietState ? quietMetadataError : null) || observation.error;
        let status;
        if (!enabled) status = 'Registro de ' + observation.label + ' desactivado';
        else if (observationError !== null) status = 'Lectura de ' + observation.label + ' no disponible';
        else if (!fresh) status = 'Esperando datos de ' + observation.label;
        else if (!current[observation.name]) status = 'Registrando ' + observation.label + '; sin efecto presente';
        else if (observation === quietState) status = 'Quiet detectado; sólo registro';
        else if (observation === silencedState) status = 'Silenced detectado; filtro local experimental';
        else status = observation.label + ' detectado; comprobar los contadores ' +
            (observation === confuseState ? 'de movimiento' : 'visuales');
        return { enabled, available: fresh && observationError === null,
            status, error: observationError, fresh, age_ms: age,
            current: fresh ? Object.assign({}, current) : null,
            detections: observation.detections, revision: observation.revision,
            last_episode: observation.last_episode === null ? null : JSON.parse(JSON.stringify(observation.last_episode)) };
    }
    function snapshot() {
        const association = getAssociation();
        const quietObservation = observationSnapshot(quietState);
        const selected = protections.filter(protection => protection.enabled());
        const failures = selected.filter(protection => protection.error !== null);
        const error = failures.length === 0 ? null : failures.map(protection =>
            protection.label + ': ' + protection.error).join('\n');
        const age = association === null ? Infinity : Date.now() - association.gui_seen_at;
        const fresh = association !== null && association.identity === identity && age >= 0 && age <= 250;
        let status;
        if (!options.enabled) status = 'Desactivado';
        else if (selected.length === 0) status = enabledQuietRecording() ? quietObservation.status : 'Sin protecciones seleccionadas';
        else if (failures.length === selected.length) status = 'Debuffs detenido: ' + error.split('\n')[0];
        else if (!fresh) status = 'Esperando datos del jugador';
        else if (!state.foreground) status = 'Pausado: vuelve a la ventana del juego';
        else status = selected.filter(protection => protection.error === null).map(protection =>
            protection.label).join(' / ') + (options.active ? ' activo' : ' en observación; sin cambios');
        return { enabled: options.enabled, active: hasProtection() && options.active,
            confuse: options.confuse, blind: options.blind, silenced: options.silenced,
            silenced_experimental: true, server_acceptance_verified: false,
            quiet: false, record_quiet: options.record_quiet,
            quiet_supported: false, quiet_reason: quietReason, blind_supported: true, status, error,
            foreground: fresh && state.foreground,
            checks: movement.counters.checks, detected: movement.counters.detected, blocked: movement.counters.blocked,
            visual_checks: visual.counters.checks, visual_detections: visual.counters.detected, visual_blocks: visual.counters.blocked,
            ability_gate_checks: ability.counters.checks, ability_gate_detections: ability.counters.detected,
            ability_gate_bypasses: ability.counters.blocked,
            age_ms: fresh && state.captured_at !== null ? Date.now() - state.captured_at : null,
            quiet_observation: quietObservation, confuse_observation: observationSnapshot(confuseState),
            blind_observation: observationSnapshot(blindState), silenced_observation: observationSnapshot(silencedState) };
    }
    // Host owns metadata attachment. The shared association binding has one
    // owner; optional Quiet metadata failures never disable movement protection.
    function prepareAssociation() {
        if (common === null) common = {
            worldPlayer: instanceField(worldClass, 'OCLNLBHDEFK', 'FKALGHJIADI', 0x48) };
    }
    function prepareConditions() {
        if (conditionMetadataAttempted) return;
        conditionMetadataAttempted = true;
        try {
            conditionField = instanceField(playerClass, 'COHCKAPOLCA', 'System.Int32[]', 0x250);
            bindings.confuse_observation.fields = { conditions: conditionField };
            bindings.blind_observation.fields = { conditions: conditionField };
            bindings.silenced_observation.fields = { conditions: conditionField };
        } catch (caught) {
            conditionMetadataError = String(caught.stack || caught);
        }
    }
    function prepareQuiet() {
        if (quietMetadataAttempted) return;
        quietMetadataAttempted = true;
        try {
            quietFields = {
                mp: instanceField(playerClass, 'FMHMGKEPIDN', 'System.Single', 0x564),
                mp_sync: instanceField(playerClass, 'GGOFEAENCCG', 'System.Int32', 0x744),
                mp_override: instanceField(playerClass, 'LHIKNJPECMB', 'System.Boolean', 0x740)
            };
            bindings.quiet_observation.fields = Object.assign({ conditions: conditionField }, quietFields);
        } catch (caught) {
            quietFields = null;
            quietMetadataError = String(caught.stack || caught);
        }
    }
    function prepareMovement() {
        const gate = typedMethod(common.livingClass, 'KDDCMDNLLKF', [], 'System.Boolean', false, 0x777aa0);
        const direction = typedMethod(playerClass, 'FOFMOMDBGGI',
            ['System.Single', 'UnityEngine.Vector2'], 'System.Void', false, 0x155f170);
        const guards = [verifyBytes(0x777aa0, '40534883ec20803daa69bd0300488bd9'),
            verifyBytes(0x155f170, '40534883ec60803d8e6ddf0200488bd9'),
            verifyBytes(0x155f1f9, 'e8a28821ff84c00f84a0000000')];
        Object.assign(bindings, { gate, direction, world_player: common.worldPlayer,
            code_guards: guards, return_address_rva: '0x155f1fe' });
        return [{ method: gate, returns: new Set([module.base.add(0x155f1fe).toString()]), installed: false }];
    }
    function prepareVisual() {
        const visualClass = resolve('Assembly-CSharp.dll', '', 'OGAIKIFOGLJ');
        const fields = {
            shake: instanceField(visualClass, 'FDIDAOHPNGH', 'Com.LuisPedroFonseca.ProCamera2D.ProCamera2DShake', 0x10),
            blur: instanceField(visualClass, 'MMJPCBEPDFI', 'DecaGames.RotMG.Graphics.Camera.PostprocessingBlur', 0x18),
            postprocessing: instanceField(visualClass, 'PAIEJEMMNKL', 'UnityEngine.PostProcessing.PostProcessingBehaviour', 0x20),
            effects: instanceField(visualClass, 'BCPFEICOKIF', 'System.Collections.Generic.Dictionary<JFMMLAALNPO,System.Boolean>', 0x28)
        };
        const getters = [
            typedMethod(common.livingClass, 'EDFAOHDOGOA', [], 'System.Boolean', false, 0x7740a0),
            typedMethod(common.livingClass, 'NIMKJAPCKPM', [], 'System.Boolean', false, 0x77a2f0)
        ];
        // Each CALL and its next instruction were verified with REA. Getter A
        // has three visual consumers; getter B has four. Other callers remain
        // outside this allowlist, even when they query the same Blind flag.
        const sites = [
            { name: 'FEOIMPPGOJB', entry: 0xe055c0, call: 0xe0564d, getter: 0,
                entry_bytes: '40534883ec20803dadc8540300488bd9', call_bytes: 'e84eea96ff4533c9440fb6c0ba01000000488bcbe8' },
            { name: 'KCAKJFFGGIN', entry: 0xe07f40, call: 0xe07fc8, getter: 0,
                entry_bytes: '40534883ec20803d639f540300488bd9', call_bytes: 'e8d3c096ff4533c9440fb6c033d2488bcbe812c5ff' },
            { name: 'ONBEBONOEIO', entry: 0xe0b7e0, call: 0xe0b868, getter: 0,
                entry_bytes: '40534883ec20803de466540300488bd9', call_bytes: 'e8338896ff4533c9440fb6c033d2488bcbe8828dff' },
            { name: 'CKJINFFLKIN', entry: 0xe046a0, call: 0xe04728, getter: 1,
                entry_bytes: '40534883ec20803d17d8540300488bd9', call_bytes: 'e8c35b97ff4533c9440fb6c0ba01000000488bcbe8' },
            { name: 'GLMKPJNPODF', entry: 0xe06050, call: 0xe060d8, getter: 1,
                entry_bytes: '40534883ec20803d2ebe540300488bd9', call_bytes: 'e8134297ff4533c9440fb6c033d2488bcbe8420700' },
            { name: 'NHHAFNPILOM', entry: 0xe0a660, call: 0xe0a6e8, getter: 1,
                entry_bytes: '40534883ec20803d5c78540300488bd9', call_bytes: 'e803fc96ff4533c9440fb6c033d2488bcbe8f29dff' },
            { name: 'OEHCMLHJGIP', entry: 0xe0b090, call: 0xe0b118, getter: 1,
                entry_bytes: '40534883ec20803d056e540300488bd9', call_bytes: 'e8d3f196ff4533c9440fb6c033d2488bcbe8c2bdff' }
        ];
        const callers = sites.map(site => typedMethod(visualClass, site.name, [], 'System.Void', false, site.entry));
        const guards = [verifyBytes(0x7740a0, '40534883ec20803d6da3bd0300488bd9'),
            verifyBytes(0x77a2f0, '40534883ec20803d5f41bd0300488bd9')];
        for (const site of sites) {
            guards.push(verifyBytes(site.entry, site.entry_bytes), verifyBytes(site.call, site.call_bytes));
        }
        bindings.blind_protection = { getters, callers, visual_fields: fields,
            code_guards: guards, return_address_rvas: sites.map(site => '0x' + (site.call + 5).toString(16)),
            source: 'Only local-player Blind getter results returning to verified visual effect consumers.',
            runtime_visual_verified: false,
            static_rea_evidence: [
                ...bindings.blind_observation.static_rea_evidence,
                'ev_fef3998b374b72f85d6b6b7e92555b5ffaf96052dee057d2338d09bd11e4c196',
                'ev_bf81a7ce42262567142fa66e5395d0370876a304435882cc2f93215166669d62',
                'ev_d511e60b969c0c6128cd8f1309930ad59edf7645a1de58041b11a9f1a11a8601',
                'ev_1c3eb3ebb79ec62d4ba80e915a50487f01e0dd69cab31f0b1f06853d3ed816dd',
                'ev_c2547f06c6b9a9256ec00ef933632f2591f2ab01e797667dc5b3297f270e7195',
                'ev_5205c0fd3e5a30d515b229aa0b0795830e5a1190a921f6de88523b3335d50123',
                'ev_dc4357b3394b77bcdc9efaad99e7dd25ae2d7f82baf780a20c23c18158055d8c',
                'ev_06371af56602bf81038fb409467b565b2d18f49951eb4dd12598bc55ac0ea71c' ] };
        return getters.map((method, index) => ({ method, installed: false,
            returns: new Set(sites.filter(site => site.getter === index).map(site =>
                module.base.add(site.call + 5).toString())) }));
    }
    function prepareAbility() {
        const gate = typedMethod(common.livingClass, 'KDGEABNFKJI', [], 'System.Boolean', false, 0x777b20);
        const callers = [
            typedMethod(playerClass, 'GCAEONBDGJF', ['System.Single', 'System.Single'], 'System.Boolean', false, 0x155f480),
            typedMethod(playerClass, 'PBABCOMDDPO', ['System.Single', 'System.Single', 'ELAINNINAMO'], 'System.Boolean', false, 0x1570520),
            typedMethod(playerClass, 'JBGFEHMHPGP', ['System.Single', 'System.Single', 'ELAINNINAMO'], 'System.Boolean', false, 0x1564890)
        ];
        // REA decoded these five calls and their following TEST AL instructions.
        // Only the Silenced getter result changes. A caller's Boolean may mean
        // "handled" rather than success, and is never replaced by this module.
        const sites = [
            { call: 0x155f54c, bytes: 'e8cf8521ff84c00f858c01000033d2488bcbe8bdaa' },
            { call: 0x1570a32, bytes: 'e8e97020ff84c00f85eb10000033d2488bcfe877d4' },
            { call: 0x1570c3e, bytes: 'e8dd6e20ff84c00f854b050000488975e0488b15ca' },
            { call: 0x1564db1, bytes: 'e86a2d21ff84c00f856a11000033d2488bcfe8f890' },
            { call: 0x1564f99, bytes: 'e8822b21ff84c00f8533050000488975f0488b155f' }
        ];
        const guards = [verifyBytes(0x777b20, '40534883ec20803d2e69bd0300488bd9'),
            verifyBytes(0x155f480, '40534883ec60803dd969df0200488bd9'),
            verifyBytes(0x1570520, '488bc444894820f30f115018f30f1148'),
            verifyBytes(0x1564890, '488bc444894820f30f115018f30f1148'),
            ...sites.map(site => verifyBytes(site.call, site.bytes))];
        bindings.silenced_protection = { gate, callers, code_guards: guards,
            return_address_rvas: sites.map(site => '0x' + (site.call + 5).toString(16)),
            experimental: true, server_acceptance_verified: false,
            source: 'Only the local-player Silenced getter returning to selected ability gates.',
            limitations: 'Bypassing this local filter does not prove an ability was used or accepted; caller results, other effects, MP, cooldowns and networking are not replaced.',
            static_rea_evidence: [...bindings.silenced_observation.static_rea_evidence,
                'ev_502885c6bef560161c11ed39bc51d612610e27a6d9dc2ab22bdae68632d0c715', 'ev_3a2f17aa822ca37da753b428b7350c0500fd7e266388afff86d1a3a90fbc9bbb', 'ev_1a91f0727e6c6481c436d0765290d84d7da8f72ed19848d8563702fd031a87f9'] };
        return [{ method: gate, installed: false,
            returns: new Set(sites.map(site => module.base.add(site.call + 5).toString())) }];
    }
    function prepareProtection(protection) {
        if (protection.gates !== null) return;
        if (common.foreground === undefined) {
            const user32 = Process.getModuleByName('user32.dll');
            common.foreground = {
                window: new NativeFunction(user32.getExportByName('GetForegroundWindow'), 'pointer', [], { abi: 'win64' }),
                process: new NativeFunction(user32.getExportByName('GetWindowThreadProcessId'),
                    'uint32', ['pointer', 'pointer'], { abi: 'win64' }),
                pid: Memory.alloc(4)
            };
        }
        if (common.livingClass === undefined) common.livingClass = resolve('Assembly-CSharp.dll', '', 'LKHPPBEGNOM');
        protection.gates = protection.prepare();
    }
    function foreground() {
        const api = common.foreground;
        const hwnd = api.window();
        if (hwnd.isNull()) return false;
        api.pid.writeU32(0);
        api.process(hwnd, api.pid);
        return api.pid.readU32() === Process.id;
    }
    function currentAssociation() {
        if (!hasActivity() || common === null || !isRunning()) return null;
        const association = getAssociation();
        const age = association === null ? Infinity : Date.now() - association.gui_seen_at;
        if (association === null || age < 0 || age > 250 ||
            !validObject(association.player, playerClass) || !validObject(association.world, worldClass) ||
            !association.world.add(common.worldPlayer.offset).readPointer().equals(association.player)) return null;
        return association;
    }
    function currentProtection(protection) {
        return !protection.enabled() || !protection.installed || protection.error !== null ? null : currentAssociation();
    }
    function fail(protection, caught) {
        if (protection.error === null) protection.error = String(caught.stack || caught);
    }
    function captureObservation(observation, association, sample, sampleError) {
        const previous = observation.current, now = sample.timestamp_ms;
        if (observation.identity !== association.identity || previous !== null && previous.map_id !== association.map_id) {
            invalidateObservation(observation, 'association_changed');
        } else if (previous !== null && (now - previous.timestamp_ms < 0 || now - previous.timestamp_ms > 250)) {
            invalidateObservation(observation, 'capture_gap');
        }
        observation.identity = association.identity;
        observation.current = sample;
        observation.error = sampleError;
        if (sampleError !== null) { interruptObservation(observation, 'read_error'); return; }
        const episode = observation.last_episode;
        if (sample[observation.name]) {
            if (episode !== null && episode.state === 'active') {
                observation.last_episode = Object.assign({}, episode, { latest: sample });
            } else {
                const before = observation.before;
                observation.detections += 1;
                observation.last_episode = { id: observation.detections, state: 'active', end_reason: null,
                    start_kind: before === null ? 'first_observed_true' : 'false_to_true',
                    before, first: sample, latest: sample, after: null };
                observation.revision += 1;
            }
        } else {
            if (episode !== null && episode.state === 'active') {
                observation.last_episode = Object.assign({}, episode,
                    { state: 'ended', end_reason: observation.name + '_cleared', after: sample });
                observation.revision += 1;
            }
            observation.before = sample;
        }
    }
    // Read one array header and only the words required by enabled observers.
    // An unavailable second word does not invalidate a valid first word. Native
    // protection does not depend on these diagnostic reads succeeding.
    function readConditionWords(player, indexes) {
        const results = new Map();
        if (conditionField === null) {
            for (const index of indexes) results.set(index, { value: null, error: conditionMetadataError });
            return results;
        }
        try {
            const array = player.add(conditionField.offset).readPointer();
            if (array.isNull()) throw Error('condition array unavailable');
            const length = array.add(0x18).readU64().toNumber();
            if (!Number.isSafeInteger(length) || length < 0) throw Error('invalid condition array length');
            for (const index of indexes) {
                try {
                    if (index >= length) throw Error('condition array does not contain word ' + index);
                    results.set(index, { value: array.add(0x20 + index * 4).readS32(), error: null });
                } catch (caught) {
                    results.set(index, { value: null, error: 'condition_word' + index + ': ' + String(caught.message || caught) });
                }
            }
        } catch (caught) {
            for (const index of indexes) results.set(index,
                { value: null, error: 'condition_word' + index + ': ' + String(caught.message || caught) });
        }
        return results;
    }
    function conditionSample(observation, condition, frame) {
        return Object.assign({
            [observation.name]: condition.value === null ? null : (condition.value & observation.mask) !== 0,
            ['condition_word' + observation.wordIndex]: condition.value }, frame);
    }
    function captureObservations(association) {
        const active = observations.filter(observation => observation.enabled());
        const words = readConditionWords(association.player, new Set(active.map(observation => observation.wordIndex)));
        const base = { map_id: association.map_id, map_name: association.map_name ?? null,
            hp: association.hp ?? null, max_hp: association.max_hp ?? null, timestamp_ms: Date.now() };
        for (const observation of active) {
            const protection = observation.protection;
            if (protection === null) continue;
            const condition = words.get(observation.wordIndex), sample = conditionSample(observation, condition, base);
            for (const [counter, field] of Object.entries(protection.sampleFields)) sample[field] = protection.counters[counter];
            captureObservation(observation, association, sample, condition.error);
        }
        if (enabledQuietRecording() && quietFields !== null) {
            const condition = words.get(quietState.wordIndex);
            const sample = Object.assign(conditionSample(quietState, condition, base),
                { mp: null, mp_sync: null, mp_override: null });
            const failures = condition.error === null ? [] : [condition.error];
            function read(name, operation) {
                try { sample[name] = operation(); }
                catch (caught) { failures.push(name + ': ' + String(caught.message || caught)); }
            }
            read('mp', () => {
                const value = association.player.add(quietFields.mp.offset).readFloat();
                if (!Number.isFinite(value)) throw Error('non-finite MP');
                return value;
            });
            read('mp_sync', () => association.player.add(quietFields.mp_sync.offset).readS32());
            read('mp_override', () => {
                const value = association.player.add(quietFields.mp_override.offset).readU8();
                if (value !== 0 && value !== 1) throw Error('invalid Boolean');
                return value === 1;
            });
            captureObservation(quietState, association, sample, failures.length === 0 ? null : failures.join('; '));
        }
    }
    function capture(association) {
        let fresh;
        try {
            fresh = currentAssociation();
            if (association === null || fresh === null || association.identity !== fresh.identity ||
                association.map_id !== fresh.map_id) { invalidate('association_unavailable'); return; }
        } catch (caught) {
            for (const protection of protections) if (protection.enabled()) fail(protection, caught);
            for (const observation of observations) if (observation.enabled()) observation.error = String(caught.stack || caught);
            invalidate('association_read_error');
            return;
        }
        captureObservations(fresh);
        if (!protections.some(protection => protection.enabled() && protection.installed && protection.error === null)) return;
        try {
            if (identity !== fresh.identity) { generation += 1; identity = fresh.identity; }
            state.foreground = foreground();
            state.captured_at = Date.now();
        } catch (caught) { for (const protection of protections) if (protection.enabled()) fail(protection, caught); }
    }
    function installGate(protection, gate) {
        if (gate.installed) return;
        ownedHooks.push(Interceptor.attach(ptr(gate.method.native_address), {
            onEnter(args) {
                this.debuffCall = null;
                if (!protection.enabled() || !gate.returns.has(this.returnAddress.toString())) return;
                try {
                    const association = currentProtection(protection);
                    if (association === null || !args[0].equals(association.player) || !foreground()) return;
                    this.debuffCall = { identity: association.identity, map_id: association.map_id,
                        player: association.player, generation };
                } catch (caught) { fail(protection, caught); }
            },
            onLeave(result) {
                const call = this.debuffCall;
                if (!protection.enabled() || call === null || call === undefined || call.generation !== generation) return;
                try {
                    const association = currentProtection(protection);
                    if (association === null || call.identity !== association.identity ||
                        call.map_id !== association.map_id || !call.player.equals(association.player) || !foreground()) return;
                    protection.counters.checks += 1;
                    if ((result.toInt32() & 0xff) === 0) return;
                    protection.counters.detected += 1;
                    if (options.active) { result.replace(ptr(0)); protection.counters.blocked += 1; }
                } catch (caught) { fail(protection, caught); }
            }
        }));
        gate.installed = true;
    }
    function install() {
        if (!hasActivity()) return;
        prepareAssociation();
        prepareConditions();
        if (enabledQuietRecording()) prepareQuiet();
        for (const protection of protections) {
            if (!protection.enabled() || protection.installed) continue;
            try {
                prepareProtection(protection);
                for (const gate of protection.gates) installGate(protection, gate);
                protection.installed = true;
            } catch (caught) { fail(protection, caught); throw caught; }
        }
    }
    function configure(nextOptions) {
        const next = validate(nextOptions); // Reject before any state change.
        if (Object.keys(next).some(key => next[key] !== options[key])) {
            // Editing Confuse does not turn a continuous Quiet interval into a
            // new onset. Recording configuration changes interrupt explicitly.
            generation += 1; identity = null; state.foreground = false; state.captured_at = null;
            if (next.enabled !== options.enabled || next.record_quiet !== options.record_quiet) {
                invalidateObservation(quietState, 'recording_configuration_changed');
            }
            if (next.enabled !== options.enabled || next.confuse !== options.confuse) {
                invalidateObservation(confuseState, 'recording_configuration_changed');
            }
            if (next.enabled !== options.enabled || next.blind !== options.blind) {
                invalidateObservation(blindState, 'recording_configuration_changed');
            }
            if (next.enabled !== options.enabled || next.silenced !== options.silenced) {
                invalidateObservation(silencedState, 'recording_configuration_changed');
            }
            options = next;
            bindings.writes = hasProtection() && options.active ? 'Scoped boolean return only' : 'none';
        }
        install();
        return snapshot();
    }
    if (hasActivity()) {
        prepareAssociation();
        prepareConditions();
        if (enabledQuietRecording()) prepareQuiet();
        for (const protection of protections) if (protection.enabled()) prepareProtection(protection);
    }
    return { bindings, invalidate, snapshot, capture, install, configure };
}
