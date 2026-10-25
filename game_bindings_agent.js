'use strict';

// Read the game's own resolved KeyCode results, including unsaved defaults.
// This observer never invokes GetKeyCode or changes its arguments/return value.
function createGameBindingsBridge(context) {
    const { resolve, instanceField, typedMethod, validObject, ownedHooks,
        getAssociation, isRunning, playerClass } = context;
    const inputClass = resolve('Assembly-CSharp.dll', 'DecaGames.RotMG.Managers.Options', 'InputManager');
    const settingsClass = resolve('Assembly-CSharp.dll', 'DecaGames.RotMG.UI.Managers', 'SettingsManager');
    const inputField = instanceField(playerClass, 'BOGNJLHEBGL',
        'DecaGames.RotMG.Managers.Options.InputManager', 0x620);
    const settingsField = instanceField(inputClass, 'FMALFKOBLOI',
        'DecaGames.RotMG.UI.Managers.SettingsManager', 0x58);
    const getter = typedMethod(settingsClass, 'GetKeyCode', ['KHOOEAMAAIM'],
        'UnityEngine.KeyCode', false, 0x1cdd310);
    let latest = {};
    let identity = null;
    let error = null;
    function invalidate() { latest = {}; identity = null; error = null; }
    function install() {
        ownedHooks.push(Interceptor.attach(ptr(getter.native_address), {
            onEnter(args) {
                this.binding = null;
                if (!isRunning()) return;
                const code = args[1].toInt32();
                if (code !== 8 && code !== 12) return;
                try {
                    const association = getAssociation();
                    const age = association === null ? Infinity : Date.now() - association.gui_seen_at;
                    if (association === null || age < 0 || age > 250) return;
                    const input = association.player.add(inputField.offset).readPointer();
                    if (!validObject(input, inputClass) ||
                        !input.add(settingsField.offset).readPointer().equals(args[0]) ||
                        !validObject(args[0], settingsClass)) return;
                    if (identity !== association.identity) { latest = {}; identity = association.identity; }
                    this.binding = { name: code === 8 ? 'autofire' : 'nexus',
                        identity: association.identity, map_id: association.map_id };
                } catch (caught) { error = String(caught); }
            },
            onLeave(result) {
                const call = this.binding;
                if (call === null || !isRunning()) return;
                const association = getAssociation();
                if (association === null || association.identity !== call.identity ||
                    association.map_id !== call.map_id) return;
                latest[call.name] = { unity_code: result.toInt32(),
                    source: 'SettingsManager.GetKeyCode', captured_at: Date.now() };
            }
        }));
    }
    return { install, invalidate,
        bindings: { inputField, settingsField, getter, enum_values: { autofire: 8, nexus: 12 } },
        snapshot() {
            const association = getAssociation();
            if (association === null || association.identity !== identity) return {};
            const result = {};
            for (const [name, value] of Object.entries(latest)) {
                result[name] = { unity_code: value.unity_code, source: value.source,
                    age_ms: Date.now() - value.captured_at };
            }
            if (error !== null) result.error = error;
            return result;
        }
    };
}
