"""Portable entry point, including an explicit offline distribution self-test."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.metadata
import hashlib
import json
from pathlib import Path
import sys


def check_release_assets(app_root: Path, resource_root: Path, version: str) -> dict:
    """Check the builder's retained selection; never execute script assets."""
    manifest = json.loads((app_root / "BUILD-MANIFEST.json").read_text(encoding="utf-8"))
    if (manifest.get("format") != 1 or manifest.get("application") != "MiRealmBot"
            or manifest.get("version") != version):
        raise RuntimeError("La versión del manifiesto no corresponde a esta aplicación")
    selected = manifest.get("resource_files")
    if not isinstance(selected, list) or not selected:
        raise RuntimeError("El manifiesto no conserva la selección de recursos")
    checked = {}
    for relative in selected:
        if (not isinstance(relative, str) or not relative or "\\" in relative
                or Path(relative).is_absolute() or any(part in ("..", "") for part in relative.split("/"))):
            raise RuntimeError("El manifiesto contiene un recurso sin ruta relativa válida")
        path = resource_root / relative
        if not path.is_file():
            raise RuntimeError(f"Falta el recurso incluido: {relative}")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        retained = manifest.get("files", {}).get("_internal/" + relative, {})
        if not data or retained.get("sha256") != digest or retained.get("bytes") != len(data):
            raise RuntimeError(f"El recurso no coincide con el manifiesto: {relative}")
        checked[relative] = digest
    return {"manifest_version": manifest["version"], "checked_resources": checked}


def portable_self_test() -> int:
    """Exercise bundled imports, settings and hidden UI without game access."""
    from app_paths import (APP_ROOT, RESOURCE_ROOT, DEFAULT_CONFIG_PATH,
                           LEGACY_CONFIG_PATH, ensure_default_config, resource_path)
    report = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "game_accessed": False,
        "input_sent": False,
        "network_accessed": False,
        "updates_executed": False,
    }
    output = APP_ROOT / "analysis" / "portable-self-test.json"
    try:
        import frida
        import tkinter as tk
        from app_version import VERSION
        import autoaim_settings
        import debuffs_settings
        import game_settings
        import client_updates
        import runtime_control
        import runtime_logs
        import update_core
        from bot_runtime import config_from_mapping, load_config
        import client_ui

        report["version"] = VERSION
        report.update(check_release_assets(APP_ROOT, RESOURCE_ROOT, VERSION))
        canonical_existed = DEFAULT_CONFIG_PATH.exists()
        legacy_bytes = LEGACY_CONFIG_PATH.read_bytes() if LEGACY_CONFIG_PATH.is_file() else None
        config_path = ensure_default_config()
        config = load_config(config_path)
        config_bytes_before_ui = config_path.read_bytes()
        migrated = not canonical_existed and legacy_bytes is not None
        if migrated:
            backups = list(APP_ROOT.glob("autonexus.json.migration.*.bak"))
            if (config_path.read_bytes() != legacy_bytes
                    or LEGACY_CONFIG_PATH.read_bytes() != legacy_bytes
                    or not any(path.read_bytes() == legacy_bytes for path in backups)):
                raise RuntimeError("La migración no conservó la configuración anterior")
        agent = resource_path("bot_agent.js")
        if not agent.is_file() or agent.stat().st_size == 0:
            raise RuntimeError("Falta el agente incluido")
        if "replay_autonexus" in sys.modules:
            raise RuntimeError("La aplicación portátil no debe cargar el reproductor de registros")
        if client_ui.VERSION != VERSION or client_updates.VERSION != VERSION or update_core.VERSION != VERSION:
            raise RuntimeError("La interfaz y las actualizaciones tienen versiones distintas")
        # Supply parsed values instead of inspecting the user's game registry.
        bindings = game_settings.parse_game_bindings({})
        runtime_called = False

        def block_runtime(*args, **kwargs):
            nonlocal runtime_called
            runtime_called = True
            raise RuntimeError("El self-test no puede iniciar la integración del juego")

        root = tk.Tk()
        app = None
        try:
            root.withdraw()
            app = client_ui.App(root, config_path=config_path,
                                runtime_runner=block_runtime,
                                settings_reader=lambda: bindings,
                                auto_check_updates=False)
            root.update_idletasks()
            tcl_version = str(root.tk.call("info", "patchlevel"))
            tk_version = str(root.tk.call("package", "require", "Tk"))
            tabs = [app.notebook.tab(tab, "text") for tab in app.notebook.tabs()]
            if tabs != ["Juego en vivo", "AutoNexus", "AutoAim", "Debuffs", "Actualizaciones"]:
                raise RuntimeError(f"Las pestañas no corresponden a esta versión: {tabs}")
            app.draft.validate()
            live_widgets = list(app.live_tab.winfo_children())
            for widget in live_widgets:
                live_widgets.extend(widget.winfo_children())
            start_text = str(app.start_button.cget("text"))
            start_buttons = [widget for widget in live_widgets
                             if widget.winfo_class() == "TButton"
                             and str(widget.cget("text")) == "Iniciar bot"]
            mode_selector = (any(widget.winfo_class() in ("TRadiobutton", "Radiobutton")
                                 for widget in live_widgets)
                             or any(hasattr(app, name) for name in (
                                 "mode_var", "observe_radio", "active_radio")))
            if start_text != "Iniciar bot" or len(start_buttons) != 1 or mode_selector:
                raise RuntimeError("Juego en vivo debe mostrar un único Iniciar bot y ningún selector de modo")
            all_widgets = list(root.winfo_children())
            for widget in all_widgets:
                all_widgets.extend(widget.winfo_children())
            save_buttons = [widget for widget in all_widgets
                            if widget.winfo_class() in ("TButton", "Button")
                            and "guardar" in str(widget.cget("text")).casefold()]
            if save_buttons:
                raise RuntimeError("La interfaz no debe mostrar botones Guardar; los cambios se guardan automáticamente")
            quiet_disabled = app.quiet_check.instate(("disabled",))
            quiet_value = bool(app.draft.quiet.get())
            if not quiet_disabled or quiet_value:
                raise RuntimeError("Quiet debe permanecer desactivado y su control deshabilitado")
            if root.state() != "withdrawn":
                raise RuntimeError("La comprobación de interfaz debe permanecer oculta")
            if runtime_called or app.runtime_thread is not None or app.updates_panel.busy:
                raise RuntimeError("Abrir la interfaz no debe iniciar el juego ni buscar actualizaciones")
            ui_title = root.title()
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()
        if config_path.read_bytes() != config_bytes_before_ui:
            raise RuntimeError("Abrir y cerrar la interfaz no debe reescribir la configuración existente")
        default_aim = autoaim_settings.parse_autoaim()
        default_debuffs = debuffs_settings.parse_debuffs()
        default_config = config_from_mapping({})
        if default_aim.enabled or default_aim.prioritize_bosses or default_aim.ignore_walls:
            raise RuntimeError("AutoAim y sus opciones no deben activarse por defecto")
        if not default_config.autonexus_enabled or default_debuffs.enabled or default_debuffs.quiet:
            raise RuntimeError("Los módulos no tienen los valores predeterminados esperados")
        report.update({
            "ok": True,
            "ui_version": client_ui.VERSION,
            "update_version": update_core.VERSION,
            "modules_imported": ["autoaim_settings", "debuffs_settings", "game_settings", "client_updates", "update_core", "runtime_logs", "runtime_control", "bot_runtime"],
            "ui_tabs": tabs,
            "ui_title": ui_title,
            "ui_checked_while_hidden": True,
            "ui_starts_stopped": True,
            "ui_start_button_text": start_text,
            "ui_start_button_count": len(start_buttons),
            "ui_mode_selector_present": mode_selector,
            "ui_save_button_count": len(save_buttons),
            "ui_quiet_disabled": quiet_disabled,
            "ui_quiet_value": quiet_value,
            "configuration_unchanged_on_ui_open_close": True,
            "runtime_runner_invoked": runtime_called,
            "ui_configuration_validated": True,
            "replay_module_loaded": False,
            "default_runtime_log_limits": {
                "max_file_bytes": runtime_logs.MAX_FILE_BYTES,
                "backup_count": runtime_logs.BACKUP_COUNT,
                "max_record_bytes": runtime_logs.MAX_RECORD_BYTES,
                "total_bytes": runtime_logs.TOTAL_LOG_BYTES,
            },
            "default_autoaim_enabled": default_aim.enabled,
            "default_autonexus_enabled": default_config.autonexus_enabled,
            "default_debuffs": debuffs_settings.debuffs_mapping(default_debuffs),
            "default_prioritize_bosses": default_aim.prioritize_bosses,
            "default_ignore_walls": default_aim.ignore_walls,
            "game_preferences_accessed": False,
            "binding_parser_controls": sorted(bindings),
            "frida_version": frida.__version__,
            "frida_metadata_version": importlib.metadata.version("frida"),
            "tcl_version": tcl_version,
            "tk_version": tk_version,
            "resources_beside_modules": agent.parent == RESOURCE_ROOT,
            "configuration_beside_executable": APP_ROOT == Path(sys.executable).resolve().parent,
            "canonical_configuration_name": config_path.name,
            "configuration_migrated_from_legacy": migrated,
            "legacy_configuration_preserved": legacy_bytes is None or LEGACY_CONFIG_PATH.read_bytes() == legacy_bytes,
            "threshold": config.threshold,
            "prediction_enabled": config.prediction_enabled,
        })
        code = 0
    except Exception as error:
        report.update({"ok": False, "error": str(error), "error_type": type(error).__name__})
        code = 1
    output.parent.mkdir(parents=True, exist_ok=True)
    report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code


def main() -> int:
    if sys.argv[1:] == ["--portable-self-test"]:
        return portable_self_test()
    if sys.argv[1:]:
        raise SystemExit("MiRealmBot se abre sin parámetros; --portable-self-test sólo comprueba la descarga sin acceder al juego.")
    from client_ui import main as client_main
    return client_main()


if __name__ == "__main__":
    raise SystemExit(main())
