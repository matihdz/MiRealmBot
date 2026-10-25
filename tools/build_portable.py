"""Build a Windows x64 portable ZIP from an explicit public-source allowlist.

Uses the current virtual environment; never installs software, copies captures,
or replaces an existing build. Run after installing requirements-build.txt.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import ssl
import subprocess
import sys
from uuid import uuid4
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SOURCES = (
    "app_paths.py", "autonexus_core.py", "autonexus_controller.py",
    "damage_prediction.py", "windows_nexus.py", "probe_game.py",
    "bot_runtime.py", "client_ui.py", "runtime_logs.py", "runtime_control.py",
    "app_version.py", "update_core.py", "client_updates.py",
    "autoaim_settings.py", "game_settings.py", "debuffs_settings.py",
)
RESOURCE_FILES = (
    "bot_agent.js", "autonexus_agent.js", "debuffs_agent.js",
    "updater_worker.ps1",
    "game_bindings_agent.js", "autoaim_agent.js",
)
DOCUMENT_FILES = ("LEEME-PRIMERO.txt", "docs/USO.md")
PINNED_PACKAGES = {
    "frida": "17.24.3", "pyinstaller": "6.22.3",
    "pyinstaller-hooks-contrib": "2026.9", "altgraph": "0.17.5",
    "packaging": "26.3", "pefile": "2024.8.26",
    "pywin32-ctypes": "0.2.3", "setuptools": "84.2.1",
}
REQUIRED_PAYLOAD = (
    "MiRealmBot.exe", "mirealmbot.example.json", "autonexus.json", "LEEME-PRIMERO.txt", "docs/USO.md",
    "THIRD-PARTY-NOTICES.txt", "licenses/Python-LICENSE.txt",
    "licenses/Frida-COPYING.txt", "licenses/PyInstaller-COPYING.txt",
    "licenses/Tk-license.terms", "licenses/Tcl-8.6.15.txt", "licenses/LGPL-2.0.txt",
    "licenses/OpenSSL-3.0.16-LICENSE.txt", "licenses/OpenSSL-3.0.16-AUTHORS.md",
    *(f"_internal/{relative}" for relative in RESOURCE_FILES),
    "_internal/python312.dll", "_internal/frida/_frida.pyd",
    "_internal/tcl86t.dll", "_internal/tk86t.dll", "_internal/_tkinter.pyd",
)
FORBIDDEN_NAMES = {
    "gameassembly.dll", "librb.dll", "realmbot_exalted.exe",
    "global-metadata.dat", "rotmg exalt.exe", "autonexus.example.json",
    "replay_autonexus.py", "demo-combat.jsonl",
    "mirealmbot.json", "live_autonexus.py",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_environment() -> dict[str, str]:
    if sys.platform != "win32" or platform.machine().upper() not in ("AMD64", "X86_64"):
        raise RuntimeError("Construye esta descarga en Windows x64.")
    if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        raise RuntimeError("La construcción requiere Python 3.12 x64.")
    if sys.prefix == sys.base_prefix:
        raise RuntimeError("Usa un entorno virtual; no una instalación global de Python.")
    versions = {name: importlib.metadata.version(name) for name in PINNED_PACKAGES}
    if versions != PINNED_PACKAGES:
        mismatches = {name: value for name, value in versions.items()
                      if value != PINNED_PACKAGES[name]}
        raise RuntimeError(f"Instala requirements-build.txt; versiones inesperadas: {mismatches}")
    import tkinter
    if tkinter.Tcl().eval("info patchlevel") != "8.6.15":
        raise RuntimeError("La licencia Tcl incluida corresponde a Tcl 8.6.15; revisa la nueva versión antes de construir.")
    # CPython exposes OpenSSL 3's patch level in the fourth legacy tuple slot.
    if ssl.OPENSSL_VERSION_INFO != (3, 0, 0, 16, 0):
        raise RuntimeError("Las licencias OpenSSL incluidas corresponden a 3.0.16; revisa la nueva versión antes de construir.")
    return versions


def copy_relative(source_root: Path, target_root: Path, relative: str) -> None:
    source = source_root / relative
    if not source.is_file():
        raise FileNotFoundError(f"Falta el archivo público requerido: {relative}")
    destination = target_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def collect_licenses(app: Path) -> None:
    target = app / "licenses"
    target.mkdir()
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", target / "Python-LICENSE.txt")
    shutil.copyfile(Path(sys.base_prefix) / "tcl/tk8.6/license.terms", target / "Tk-license.terms")
    for name in ("Tcl-8.6.15.txt", "LGPL-2.0.txt",
                 "OpenSSL-3.0.16-LICENSE.txt", "OpenSSL-3.0.16-AUTHORS.md"):
        shutil.copyfile(ROOT / "packaging/licenses" / name, target / name)
    for name, prefix in (("frida", "Frida"), ("pyinstaller", "PyInstaller")):
        dist = importlib.metadata.distribution(name)
        original = "COPYING" if name == "frida" else "COPYING.txt"
        candidates = [item for item in dist.files or () if item.name == original]
        if len(candidates) != 1:
            raise RuntimeError(f"No se identificó la licencia instalada de {name}")
        shutil.copyfile(dist.locate_file(candidates[0]), target / f"{prefix}-COPYING.txt")
    for name in PINNED_PACKAGES:
        if name in ("frida", "pyinstaller"):
            continue
        dist = importlib.metadata.distribution(name)
        for item in dist.files or ():
            if "dist-info" not in str(item):
                continue
            if item.name.lower().startswith(("license", "licence", "copying", "notice")):
                # Vendored dependencies can each own LICENSE. Retain their
                # package-relative identity rather than flattening basenames.
                output = target / f"build-tool-{name}" / Path(str(item))
                if output.exists():
                    raise RuntimeError(f"Dos licencias tienen el mismo nombre: {output.name}")
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dist.locate_file(item), output)
    shutil.copyfile(ROOT / "packaging/THIRD-PARTY-NOTICES.txt", app / "THIRD-PARTY-NOTICES.txt")


def validate_distribution(app: Path) -> None:
    """Reject missing runtime assets and accidental private/game payloads."""
    missing = [relative for relative in REQUIRED_PAYLOAD if not (app / relative).is_file()]
    if missing:
        raise RuntimeError(f"La descarga está incompleta: {missing}")
    config_bytes = (ROOT / "mirealmbot.example.json").read_bytes()
    if any((app / name).read_bytes() != config_bytes
           for name in ("mirealmbot.example.json", "autonexus.json")):
        raise RuntimeError("La descarga debe usar la configuración pública de ejemplo.")
    unexpected = []
    for path in app.rglob("*"):
        relative = path.relative_to(app)
        if path.name.lower() in FORBIDDEN_NAMES:
            unexpected.append(relative.as_posix())
        if any(part.lower() in ("analysis", ".venv", ".git", "__pycache__", "examples") for part in relative.parts):
            unexpected.append(relative.as_posix())
        if path.suffix.lower() == ".bak":
            unexpected.append(relative.as_posix())
        if path.suffix.lower() == ".jsonl":
            unexpected.append(relative.as_posix())
    if unexpected:
        raise RuntimeError(f"Archivos que no deben publicarse: {sorted(set(unexpected))}")


def archive_distribution(app: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(app.parent).as_posix())


def build(build_id: str) -> dict:
    # Keep version comparison and the downloaded manifest on the same source.
    sys.path.insert(0, str(ROOT))
    from app_version import VERSION
    versions = validate_environment()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", build_id):
        raise ValueError("build-id sólo puede contener letras, números, guion y subrayado.")
    # Reserve independent directories. Existing builds are never cleaned or replaced.
    output = ROOT / "dist" / build_id
    work = ROOT / "build" / build_id
    output.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True, exist_ok=False)
    source = work / "source"
    source.mkdir()
    selected = RUNTIME_SOURCES + RESOURCE_FILES
    for relative in selected:
        copy_relative(ROOT, source, relative)
    # The builder owns the asset allowlist. The spec consumes this exact
    # selection instead of maintaining a second list that can drift.
    (source / "bundle-resources.json").write_text(
        json.dumps(RESOURCE_FILES, ensure_ascii=False) + "\n", encoding="utf-8")
    shutil.copyfile(ROOT / "packaging/launcher.py", source / "launcher.py")
    for relative in DOCUMENT_FILES + ("mirealmbot.example.json",):
        if not (ROOT / relative).is_file():
            raise FileNotFoundError(f"Falta {relative}")
    environment = os.environ.copy()
    environment["MIREALMBOT_BUILD_SOURCE_ROOT"] = str(source)
    command = [
        sys.executable, "-m", "PyInstaller",
        "--distpath", str(output), "--workpath", str(work / "pyinstaller"),
        str(ROOT / "packaging/MiRealmBot.spec"),
    ]
    print(f"Construyendo MiRealmBot ({build_id})…", flush=True)
    with (work / "pyinstaller.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=source, env=environment,
                                stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"PyInstaller falló ({result.returncode}); consulta build/{build_id}/pyinstaller.log")
    app = output / "MiRealmBot"
    copy_relative(ROOT, app, "mirealmbot.example.json")
    # Installed <=0.3.1 updaters require this name and copy the user's older
    # configuration over it. The canonical file is created by migration on
    # first launch, so it must never be preseeded by a release ZIP.
    shutil.copyfile(ROOT / "mirealmbot.example.json", app / "autonexus.json")
    for relative in DOCUMENT_FILES:
        copy_relative(ROOT, app, relative)
    if (ROOT / "LICENSE").is_file():
        copy_relative(ROOT, app, "LICENSE")
    collect_licenses(app)
    validate_distribution(app)
    manifest = {
        "format": 1,
        "application": "MiRealmBot",
        "version": VERSION,
        "build_id": build_id,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": "Windows-x64",
        "python": platform.python_version(),
        "openssl": ssl.OPENSSL_VERSION,
        "dependencies": versions,
        "build_options": {"layout": "onedir", "console": False, "upx": False},
        "source_files": {relative: sha256(source / relative) for relative in selected},
        "resource_files": list(RESOURCE_FILES),
        "launcher_sha256": sha256(source / "launcher.py"),
        "configuration_source": "mirealmbot.example.json",
        "configuration_sha256": sha256(app / "autonexus.json"),
        "files": {path.relative_to(app).as_posix(): {"sha256": sha256(path), "bytes": path.stat().st_size}
                  for path in sorted(app.rglob("*")) if path.is_file()},
    }
    (app / "BUILD-MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    destination = output / "MiRealmBot-Windows-x64.zip"
    archive_distribution(app, destination)
    digest = sha256(destination)
    checksum = output / "SHA256SUMS.txt"
    checksum.write_text(f"{digest}  {destination.name}\n", encoding="ascii")
    receipt = {"zip": str(destination), "bytes": destination.stat().st_size,
               "sha256": digest, "executable": str(app / "MiRealmBot.exe"),
               "checksums": str(checksum), "manifest": str(app / "BUILD-MANIFEST.json")}
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    args = parser.parse_args()
    build(args.build_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
