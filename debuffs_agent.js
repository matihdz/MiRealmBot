'use strict';

// Independent client movement protection. No persistent condition, mana or
// network state is written. Only the bool returned to the verified local-player
// movement call site can be replaced; other callers retain the game's result.
function createDebuffsBridge(context) {
    const { module, resolve, instanceField, typedMethod, verifyBytes, validObject,
        ownedHooks, isRunning, getAssociation, playerClass, worldClass } = context;
    const quietReason = 'AntiQuiet no disponible: no se ha verificado una ruta que evite su efecto sobre el maná y las habilidades.';
    function validate(next) {
        if (next === null || typeof next !== 'object' || Array.isArray(next) ||
            Object.keys(next).some(key => !['enabled', 'active', 'confuse', 'quiet'].includes(key)) ||
            ['enabled', 'active', 'confuse', 'quiet'].some(key => typeof next[key] !== 'boolean')) {
            throw Error('Debuffs requires explicit enabled, active, confuse and quiet booleans.');
        }
        if (next.quiet) throw Error(quietReason);
        return { enabled: next.enabled, active: next.active, confuse: next.confuse, quiet: false };
    }
    let options = validate(context.options);
    const enabledProtection = () => options.enabled && options.confuse;
    let generation = 0;
    let identity = null;
    let error = null;
    let installed = false;
    let native = null;
    const state = { foreground: false, checks: 0, detected: 0, blocked: 0, captured_at: null };
    const bindings = { quiet_supported: false, quiet_reason: quietReason,
        source: 'Local-player movement condition result at one verified call site.',
        writes: enabledProtection() && options.active ? 'Scoped boolean return only' : 'none',
        condition_field_writes: false, mana_writes: false, network_changes: false,
        managed_invocations: false, runtime_combat_verified: false,
        static_rea_evidence: [
            'ev_31b47b04e067dbaa56b94ceb43712103a231ebefcbfe83dd0ae7668c5dfcd9bde',
            'ev_3d89fb32874582a47265b781fa58772b85ad4ea61a020230b86bd2fe71a830033',
            'ev_cc064b400188f59c5275a09ce99d60ba7e836a099b165c0b225351b5dd6660b38',
            'ev_9b08e612374910d3171d0c64016dc054914de26927078c5b05f2ada23766aff36'
        ] };
    function invalidate() {
        generation += 1;
        identity = null;
        state.foreground = false;
        state.captured_at = null;
    }
    function snapshot() {
        const association = getAssociation();
        const age = association === null ? Infinity : Date.now() - association.gui_seen_at;
        const fresh = association !== null && association.identity === identity && age >= 0 && age <= 250;
        let status;
        if (!options.enabled) status = 'Desactivado';
        else if (!options.confuse) status = 'Sin protecciones seleccionadas';
        else if (error !== null) status = 'Debuffs detenido: ' + error.split('\n')[0];
        else if (!fresh) status = 'Esperando datos del jugador';
        else if (!state.foreground) status = 'Pausado: vuelve a la ventana del juego';
        else status = options.active ? 'AntiConfuse activo para el movimiento' : 'Observando Confuse; sin cambios';
        return { enabled: options.enabled, active: enabledProtection() && options.active,
            confuse: options.confuse, quiet: false, quiet_supported: false,
            quiet_reason: quietReason, status, error, foreground: fresh && state.foreground,
            checks: state.checks, detected: state.detected, blocked: state.blocked,
            age_ms: fresh && state.captured_at !== null ? Date.now() - state.captured_at : null };
    }
    // Metadata and native APIs are prepared once on the first enabled install.
    // The host owns the IL2CPP thread attachment around install/configure.
    function prepare() {
        if (native !== null) return;
        const livingClass = resolve('Assembly-CSharp.dll', '', 'LKHPPBEGNOM');
        const worldPlayer = instanceField(worldClass, 'OCLNLBHDEFK', 'FKALGHJIADI', 0x48);
        const gate = typedMethod(livingClass, 'KDDCMDNLLKF', [], 'System.Boolean', false, 0x777aa0);
        const direction = typedMethod(playerClass, 'FOFMOMDBGGI',
            ['System.Single', 'UnityEngine.Vector2'], 'System.Void', false, 0x155f170);
        const guards = [verifyBytes(0x777aa0, '40534883ec20803daa69bd0300488bd9'),
            verifyBytes(0x155f170, '40534883ec60803d8e6ddf0200488bd9'),
            verifyBytes(0x155f1f9, 'e8a28821ff84c00f84a0000000')];
        const user32 = Process.getModuleByName('user32.dll');
        const foregroundWindow = new NativeFunction(user32.getExportByName('GetForegroundWindow'),
            'pointer', [], { abi: 'win64' });
        const windowProcess = new NativeFunction(user32.getExportByName('GetWindowThreadProcessId'),
            'uint32', ['pointer', 'pointer'], { abi: 'win64' });
        native = { gate, worldPlayer, returnAddress: module.base.add(0x155f1fe),
            foregroundWindow, windowProcess, foregroundPid: Memory.alloc(4) };
        Object.assign(bindings, { gate, direction, world_player: worldPlayer,
            code_guards: guards, return_address_rva: '0x155f1fe' });
    }
    function foreground() {
        const hwnd = native.foregroundWindow();
        if (hwnd.isNull()) return false;
        native.foregroundPid.writeU32(0);
        native.windowProcess(hwnd, native.foregroundPid);
        return native.foregroundPid.readU32() === Process.id;
    }
    function current() {
        if (!enabledProtection() || !installed || !isRunning() || error !== null) return null;
        const association = getAssociation();
        const age = association === null ? Infinity : Date.now() - association.gui_seen_at;
        if (association === null || age < 0 || age > 250 ||
            !validObject(association.player, playerClass) || !validObject(association.world, worldClass) ||
            !association.world.add(native.worldPlayer.offset).readPointer().equals(association.player)) return null;
        return association;
    }
    function fail(caught) { if (error === null) error = String(caught.stack || caught); invalidate(); }
    function capture(association) {
        try {
            const fresh = current();
            if (association === null || fresh === null || association.identity !== fresh.identity ||
                association.map_id !== fresh.map_id) return;
            if (identity !== fresh.identity) { invalidate(); identity = fresh.identity; }
            state.foreground = foreground();
            state.captured_at = Date.now();
        } catch (caught) { fail(caught); }
    }
    function install() {
        if (!enabledProtection() || installed) return;
        prepare();
        ownedHooks.push(Interceptor.attach(ptr(native.gate.native_address), {
            onEnter(args) {
                this.debuffCall = null;
                if (!enabledProtection() || !this.returnAddress.equals(native.returnAddress)) return;
                try {
                    const association = current();
                    if (association === null || !args[0].equals(association.player) || !foreground()) return;
                    this.debuffCall = { identity: association.identity, map_id: association.map_id,
                        player: association.player, generation };
                } catch (caught) { fail(caught); }
            },
            onLeave(result) {
                const call = this.debuffCall;
                if (!enabledProtection() || call === null || call === undefined || call.generation !== generation) return;
                try {
                    const association = current();
                    if (association === null || call.identity !== association.identity ||
                        call.map_id !== association.map_id || !call.player.equals(association.player) ||
                        !foreground()) return;
                    state.checks += 1;
                    if ((result.toInt32() & 0xff) === 0) return;
                    state.detected += 1;
                    if (options.active) { result.replace(ptr(0)); state.blocked += 1; }
                } catch (caught) { fail(caught); }
            }
        }));
        installed = true;
    }
    function configure(nextOptions) {
        const next = validate(nextOptions); // Reject before changing options, scopes or hooks.
        if (Object.keys(next).some(key => next[key] !== options[key])) {
            invalidate();
            options = next;
            bindings.writes = enabledProtection() && options.active ? 'Scoped boolean return only' : 'none';
        }
        try { install(); }
        catch (caught) { fail(caught); throw caught; }
        return snapshot();
    }
    // Creation occurs under the host's initial metadata thread attachment;
    // first activation from a disabled state occurs under configure's attachment.
    if (enabledProtection()) prepare();
    return { bindings, invalidate, snapshot, capture, install, configure };
}
