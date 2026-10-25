r"""Temporary IL2CPP metadata probe for this local RotMG installation.

Does not start/stop the game, install hooks, read account values, or invoke game
methods. Frida temporarily loads the adjacent metadata-only script and unloads
it before detaching. Run using the project's .venv\Scripts\python.exe.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from app_paths import APP_ROOT, resource_path

ROOT = APP_ROOT
EXPECTED_EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "RealmOfTheMadGod" / "Production" / "RotMG Exalt.exe"


class ProcessIdentity:
    """Hold a query-only process handle while checking executable and creation time."""

    def __init__(self, pid: int, expected: Path):
        if sys.platform != "win32":
            raise RuntimeError("Esta sonda requiere Windows.")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel.GetProcessTimes.restype = wintypes.BOOL
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.GetExitCodeProcess.restype = wintypes.BOOL
        self.handle = self.kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            if not self.kernel.QueryFullProcessImageNameW(self.handle, 0, path, ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.path = str(Path(path.value).resolve(strict=True))
            try:
                expected_path = expected.resolve(strict=True)
            except FileNotFoundError as error:
                raise RuntimeError("No se encontró la instalación compatible del juego. Usa RotMG Exalt desde el launcher oficial; esta versión no admite Steam ni ubicaciones personalizadas.") from error
            if os.path.normcase(self.path) != os.path.normcase(str(expected_path)):
                raise RuntimeError(f"El juego abierto usa otra instalación: {self.path}. Abre RotMG Exalt desde el launcher oficial; esta versión no admite Steam ni ubicaciones personalizadas.")
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not self.kernel.GetProcessTimes(self.handle, ctypes.byref(created), ctypes.byref(exited),
                                               ctypes.byref(kernel), ctypes.byref(user)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.created_filetime = (created.dwHighDateTime << 32) | created.dwLowDateTime
            self.assert_alive()
        except BaseException:
            self.close()
            raise

    def assert_alive(self) -> None:
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if code.value != 259:
            raise RuntimeError("El proceso del juego terminó durante la comprobación.")

    def close(self) -> None:
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventario temporal de metadatos IL2CPP del juego abierto.")
    parser.add_argument("--pid", type=int, help="PID opcional; siempre se verifican nombre y ruta.")
    parser.add_argument("--budget-seconds", type=int, default=20, choices=range(1, 61), metavar="1..60")
    parser.add_argument("--check", action="store_true", help="Solo comprobar identidad; no adjuntar Frida.")
    parser.add_argument("--class", dest="focus_classes", action="append", default=[],
                        help="Clase exacta adicional para inspeccionar; se puede repetir.")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Esta sonda requiere Windows.")
    try:
        import frida
    except ImportError:
        parser.error("Falta frida. Utiliza .venv\\Scripts\\python.exe de esta carpeta.")
    device = frida.get_local_device()
    matches = [process for process in device.enumerate_processes()
               if process.name == "RotMG Exalt.exe" and (args.pid is None or process.pid == args.pid)]
    if not matches:
        print("No se encontró RotMG Exalt.exe. Abre el juego y deja el personaje en Nexus.")
        return 2
    if len(matches) != 1:
        parser.error("Hay varios procesos del juego. Selecciona uno con --pid.")
    process = matches[0]
    identity = ProcessIdentity(process.pid, EXPECTED_EXE)
    session = None
    script = None
    errors: list[str] = []
    payload: dict[str, Any] | None = None
    result_path: Path | None = None
    try:
        print(f"Identidad verificada: PID {process.pid}, {identity.path}")
        if args.check:
            return 0
        source = resource_path("il2cpp_probe.js").read_text(encoding="utf-8")
        payload = {
            "host": {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "pid": process.pid,
                     "verified_executable_path": identity.path, "process_creation_filetime": identity.created_filetime,
                     "frida_version": frida.__version__,
                     "probe_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()},
            "snapshot": None,
            "cleanup": {"script_unloaded": False, "session_detached": False},
            "errors": errors,
        }
        identity.assert_alive()
        session = device.attach(process.pid)
        script = session.create_script(source, name="mirealmbot_metadata_probe", runtime="qjs")
        script.on("message", lambda message, data: errors.append(str(message)) if message.get("type") == "error" else None)
        script.load()
        snapshot = script.exports_sync.snapshot(args.budget_seconds * 1000, args.focus_classes)
        payload["snapshot"] = snapshot
        runtime_process = snapshot.get("process", {})
        runtime_module = snapshot.get("module", {})
        expected_module = EXPECTED_EXE.parent / "GameAssembly.dll"
        if (runtime_process.get("pid") != process.pid or
                os.path.normcase(os.path.realpath(runtime_module.get("path", ""))) !=
                os.path.normcase(str(expected_module.resolve(strict=True)))):
            raise RuntimeError(f"PID o GameAssembly no coincide: {runtime_process!r}, {runtime_module!r}")
        identity.assert_alive()
    except BaseException as error:
        errors.append(f"{type(error).__name__}: {error}")
        raise
    finally:
        if script is not None:
            try:
                script.unload()
                if payload is not None:
                    payload["cleanup"]["script_unloaded"] = True
            except Exception as error:
                errors.append(f"script.unload: {error}")
        if session is not None:
            try:
                session.detach()
                if payload is not None:
                    payload["cleanup"]["session_detached"] = True
            except Exception as error:
                errors.append(f"session.detach: {error}")
        identity.close()
        if payload is not None:
            analysis = ROOT / "analysis"
            analysis.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            result_path = analysis / f"game-metadata-{stamp}-pid{process.pid}.json"
            with result_path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            print(f"Archivo: {result_path}")
    if payload is None:
        return 1
    coverage = payload["snapshot"]["coverage"]
    print(f"Inventario: {coverage['classes_examined']} clases examinadas, "
          f"{coverage['candidate_classes']} candidatas, {coverage['fields']} campos, {coverage['methods']} métodos.")
    print(f"Completo: {coverage['complete']}; tiempo de sonda: {coverage['elapsed_ms']} ms.")
    print(f"Script descargado: {payload['cleanup']['script_unloaded']}; sesión separada: {payload['cleanup']['session_detached']}.")
    if errors:
        print("Incidencias: " + "; ".join(errors), file=sys.stderr)
        return 1
    return 0 if coverage["complete"] else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Sonda interrumpida; se solicitó descargar el script y separar la sesión.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"Sonda detenida: {error}", file=sys.stderr)
        raise SystemExit(1)
