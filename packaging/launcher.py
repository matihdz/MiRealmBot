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
    """Exercise bundled imports/assets and replay without touching the game."""
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
        import update_core
        from live_autonexus import load_config
        from replay_autonexus import load_recording, compare_thresholds

        report["version"] = VERSION
        report.update(check_release_assets(APP_ROOT, RESOURCE_ROOT, VERSION))
        config = load_config(APP_ROOT / "autonexus.json")
        agent = resource_path("autonexus_agent.js")
        if not agent.is_file() or agent.stat().st_size == 0:
            raise RuntimeError("Falta el agente incluido")
        recording = load_recording()
        compared = compare_thresholds(recording, (30, 40))
        labels = [row["event_label"] for row in compared]
        if labels != ["Golpe 2", "Golpe 1"]:
            raise RuntimeError(f"La demostración no produjo el resultado esperado: {labels}")
        root = tk.Tk()
        try:
            root.withdraw()
            root.update_idletasks()
            tcl_version = str(root.tk.call("info", "patchlevel"))
            tk_version = str(root.tk.call("package", "require", "Tk"))
        finally:
            root.destroy()
        # Importing the window must not start its worker or attach to a process.
        import client_ui  # noqa: F401
        if client_ui.VERSION != VERSION or client_updates.VERSION != VERSION or update_core.VERSION != VERSION:
            raise RuntimeError("La interfaz y las actualizaciones tienen versiones distintas")
        default_aim = autoaim_settings.parse_autoaim()
        if default_aim.enabled or default_aim.prioritize_bosses or default_aim.ignore_walls:
            raise RuntimeError("AutoAim y sus opciones no deben activarse por defecto")
        # Parse an empty supplied mapping; do not inspect the local registry.
        bindings = game_settings.parse_game_bindings({})
        report.update({
            "ok": True,
            "ui_version": client_ui.VERSION,
            "update_version": update_core.VERSION,
            "new_modules_imported": ["autoaim_settings", "game_settings", "client_updates", "update_core"],
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
            "demo_trigger_labels": labels,
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
