"""Portable entry point, including an explicit offline distribution self-test."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import sys


def portable_self_test() -> int:
    """Exercise bundled imports/assets and replay without touching the game."""
    from app_paths import APP_ROOT, RESOURCE_ROOT, resource_path
    import frida
    import tkinter as tk
    from live_autonexus import load_config
    from replay_autonexus import load_recording, compare_thresholds

    report = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "game_accessed": False,
        "input_sent": False,
    }
    output = APP_ROOT / "analysis" / "portable-self-test.json"
    try:
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
        report.update({
            "ok": True,
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
