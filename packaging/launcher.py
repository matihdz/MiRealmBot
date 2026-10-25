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
    from app_paths import APP_ROOT, RESOURCE_ROOT, resource_path
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
        import game_settings
        import client_updates
        import runtime_logs
        import update_core
        from live_autonexus import load_config
        import client_ui

        report["version"] = VERSION
        report.update(check_release_assets(APP_ROOT, RESOURCE_ROOT, VERSION))
        config = load_config(APP_ROOT / "autonexus.json")
        agent = resource_path("autonexus_agent.js")
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
            app = client_ui.App(root, config_path=APP_ROOT / "autonexus.json",
                                runtime_runner=block_runtime,
                                settings_reader=lambda: bindings,
                                auto_check_updates=False)
            root.update_idletasks()
            tcl_version = str(root.tk.call("info", "patchlevel"))
            tk_version = str(root.tk.call("package", "require", "Tk"))
            tabs = [app.notebook.tab(tab, "text") for tab in app.notebook.tabs()]
            if tabs != ["Juego en vivo", "AutoNexus", "AutoAim", "Actualizaciones"]:
                raise RuntimeError(f"Las pestañas no corresponden a esta versión: {tabs}")
            app.draft.validate()
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
        default_aim = autoaim_settings.parse_autoaim()
        if default_aim.enabled or default_aim.prioritize_bosses or default_aim.ignore_walls:
            raise RuntimeError("AutoAim y sus opciones no deben activarse por defecto")
        report.update({
            "ok": True,
            "ui_version": client_ui.VERSION,
            "update_version": update_core.VERSION,
            "modules_imported": ["autoaim_settings", "game_settings", "client_updates", "update_core", "runtime_logs"],
            "ui_tabs": tabs,
            "ui_title": ui_title,
            "ui_checked_while_hidden": True,
            "ui_starts_stopped": True,
            "ui_configuration_validated": True,
            "replay_module_loaded": False,
            "default_runtime_log_limits": {
                "max_file_bytes": runtime_logs.MAX_FILE_BYTES,
                "backup_count": runtime_logs.BACKUP_COUNT,
                "max_record_bytes": runtime_logs.MAX_RECORD_BYTES,
                "total_bytes": runtime_logs.TOTAL_LOG_BYTES,
            },
            "default_autoaim_enabled": default_aim.enabled,
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
