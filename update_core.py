"""Public GitHub release updates for the Windows portable application.

Downloads are bounded, SHA256 checked and extracted into a sibling workspace.
Installation runs only after this process exits, through the bundled PowerShell
worker. No game connection or input belongs to this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import threading
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4
import zipfile

from app_paths import (APP_ROOT, CONFIG_TEMPLATE_PATH, DEFAULT_CONFIG_PATH,
                       LEGACY_CONFIG_PATH, resource_path)
from app_version import VERSION


REPOSITORY = "matihdz/MiRealmBot"
REPOSITORY_URL = "https://github.com/" + REPOSITORY
LATEST_API = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
ZIP_NAME = "MiRealmBot-Windows-x64.zip"
CHECKSUM_NAME = "SHA256SUMS.txt"
MAX_API_BYTES = 2 * 1024 * 1024
MAX_ZIP_BYTES = 256 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 10000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_CHECKSUM_BYTES = 64 * 1024
CHUNK_SIZE = 1024 * 1024
HTTP_TIMEOUT = 30
Progress = Callable[[str, int, int], None]


class UpdateError(RuntimeError):
    """An expected update failure, suitable for displaying in the interface."""


class UpdateCancelled(UpdateError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    html_url: str
    notes: str
    zip_url: str
    zip_size: int
    checksum_url: str
    checksum_size: int
    github_digest: str | None = None


@dataclass(frozen=True)
class UpdateCheck:
    current_version: str
    release: ReleaseInfo
    available: bool


@dataclass(frozen=True)
class PreparedUpdate:
    workspace: Path
    staged_root: Path
    app_root: Path
    release: ReleaseInfo


def parse_version(value: str) -> tuple[int, int, int]:
    """Only stable x.y.z releases participate in automatic updates."""
    if not isinstance(value, str) or not re.fullmatch(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value):
        raise UpdateError("La versión de GitHub no tiene el formato estable x.y.z.")
    return tuple(int(part) for part in value.removeprefix("v").split("."))


def _cancelled(event: threading.Event | None) -> None:
    if event is not None and event.is_set():
        raise UpdateCancelled("Actualización cancelada.")


def _request(url: str):
    request = Request(url, headers={
        "User-Agent": "MiRealmBot/" + VERSION,
        "Accept": "application/vnd.github+json" if url == LATEST_API else "application/octet-stream",
    })
    try:
        response = urlopen(request, timeout=HTTP_TIMEOUT)
    except HTTPError as error:
        if error.code == 404:
            raise UpdateError("GitHub todavía no tiene una descarga estable publicada.") from error
        if error.code in (403, 429):
            raise UpdateError("GitHub limitó las consultas. Intenta de nuevo más tarde.") from error
        raise UpdateError(f"GitHub respondió con el error HTTP {error.code}.") from error
    except (URLError, TimeoutError, OSError) as error:
        raise UpdateError("No se pudo conectar con GitHub. Revisa la conexión e intenta otra vez.") from error
    final = urlsplit(response.geturl())
    if final.scheme != "https" or final.hostname not in {
        "api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
    }:
        response.close()
        raise UpdateError("La descarga fue redirigida fuera de GitHub.")
    return response


def _read_small(url: str, limit: int, cancel_event: threading.Event | None = None) -> bytes:
    _cancelled(cancel_event)
    try:
        with _request(url) as response:
            data = response.read(limit + 1)
    except (OSError, TimeoutError) as error:
        raise UpdateError("La conexión con GitHub se interrumpió. Intenta otra vez.") from error
    _cancelled(cancel_event)
    if len(data) > limit:
        raise UpdateError("La respuesta de GitHub supera el tamaño permitido.")
    return data


def _asset(assets: list, name: str, maximum: int, tag: str) -> tuple[str, int, str | None]:
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == name]
    if len(matches) != 1:
        raise UpdateError(f"La Release debe incluir exactamente un archivo {name}.")
    asset = matches[0]
    size = asset.get("size")
    expected_url = REPOSITORY_URL + "/releases/download/" + quote(tag, safe="") + "/" + name
    if (type(size) is not int or not 0 < size <= maximum or asset.get("state") != "uploaded"
            or asset.get("browser_download_url") != expected_url):
        raise UpdateError(f"El archivo {name} de GitHub está incompleto o no es válido.")
    digest = asset.get("digest")
    if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)):
        raise UpdateError("GitHub publicó una huella de descarga desconocida.")
    return expected_url, size, digest.removeprefix("sha256:").lower() if digest else None


def parse_release(payload: object) -> ReleaseInfo:
    if not isinstance(payload, dict) or payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise UpdateError("GitHub no devolvió una Release estable publicada.")
    tag = payload.get("tag_name")
    version = ".".join(str(part) for part in parse_version(tag))
    assets = payload.get("assets")
    notes = payload.get("body") or "Sin notas de la versión."
    if not isinstance(assets, list) or not isinstance(notes, str):
        raise UpdateError("La información de la Release está incompleta.")
    zip_url, zip_size, digest = _asset(assets, ZIP_NAME, MAX_ZIP_BYTES, tag)
    checksum_url, checksum_size, _ = _asset(assets, CHECKSUM_NAME, MAX_CHECKSUM_BYTES, tag)
    return ReleaseInfo(version, tag, REPOSITORY_URL + "/releases/tag/" + quote(tag, safe=""),
                       notes, zip_url, zip_size, checksum_url, checksum_size, digest)


def check_for_update(current_version: str = VERSION, *, cancel_event: threading.Event | None = None) -> UpdateCheck:
    try:
        payload = json.loads(_read_small(LATEST_API, MAX_API_BYTES, cancel_event))
    except (ValueError, UnicodeError) as error:
        raise UpdateError("GitHub devolvió información que no se pudo interpretar.") from error
    release = parse_release(payload)
    return UpdateCheck(current_version, release, parse_version(release.version) > parse_version(current_version))


def checksum_for_archive(data: bytes) -> str:
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeError as error:
        raise UpdateError("El archivo SHA256SUMS no es válido.") from error
    matches = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line.strip())
        if match and match[2] == ZIP_NAME:
            matches.append(match[1].lower())
    if len(matches) != 1:
        raise UpdateError("Falta la huella SHA256 única del ZIP de Windows.")
    return matches[0]


def _download_archive(release: ReleaseInfo, destination: Path, expected_hash: str,
                      cancel_event: threading.Event | None, progress: Progress | None) -> None:
    digest = hashlib.sha256()
    received = 0
    with _request(release.zip_url) as response, destination.open("xb") as output:
        while True:
            _cancelled(cancel_event)
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            received += len(chunk)
            if received > release.zip_size or received > MAX_ZIP_BYTES:
                raise UpdateError("El ZIP descargado supera el tamaño publicado.")
            digest.update(chunk)
            output.write(chunk)
            if progress:
                progress("Descargando", received, release.zip_size)
    if received != release.zip_size:
        raise UpdateError("La descarga quedó incompleta. Intenta otra vez.")
    if digest.hexdigest() != expected_hash:
        raise UpdateError("La huella SHA256 no coincide; no se instalará la descarga.")


def _member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    # ZipInfo normalizes Windows separators while parsing. Validate the original
    # member spelling before that normalization can hide a forbidden path.
    name = info.orig_filename
    path = PurePosixPath(name)
    mode = info.external_attr >> 16
    if (not name or "\\" in name or "\x00" in name or path.is_absolute()
            or not path.parts or path.parts[0] != "MiRealmBot"
            or any(part in ("", ".", "..") or part.rstrip(" .") != part
                   or any(character in part for character in ':<>"|?*')
                   or re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?", part, re.I)
                   for part in name.rstrip("/").split("/"))
            or stat.S_ISLNK(mode) or (mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or mode & 0o170000 == 0))
            or info.flag_bits & 1):
        raise UpdateError("El ZIP contiene una ruta, enlace o archivo no permitido.")
    if len(path.parts) == 1 and not info.is_dir():
        raise UpdateError("El ZIP no contiene la carpeta MiRealmBot esperada.")
    if any(part.casefold() in {"analysis", ".git", ".venv", "__pycache__"} for part in path.parts):
        raise UpdateError("La Release incluye datos locales que no deben distribuirse.")
    return path


def extract_archive(archive_path: Path, destination: Path, *, cancel_event: threading.Event | None = None,
                    progress: Progress | None = None) -> Path:
    """Validate the complete directory before writing any ZIP member."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_MEMBERS:
                raise UpdateError("El ZIP contiene una cantidad de archivos inesperada.")
            total = 0
            names = set()
            for info in members:
                path = _member_path(info)
                key = path.as_posix().casefold()
                if key in names:
                    raise UpdateError("El ZIP contiene rutas duplicadas para Windows.")
                names.add(key)
                if info.file_size > MAX_FILE_BYTES:
                    raise UpdateError("Un archivo del ZIP supera el tamaño permitido.")
                total += info.file_size
                if total > MAX_EXTRACTED_BYTES:
                    raise UpdateError("El ZIP expandido supera el espacio permitido.")
            destination.mkdir(parents=True, exist_ok=False)
            extracted = 0
            for info in members:
                _cancelled(cancel_event)
                target = destination.joinpath(*PurePosixPath(info.filename).parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with archive.open(info) as source, target.open("xb") as output:
                    while chunk := source.read(CHUNK_SIZE):
                        _cancelled(cancel_event)
                        written += len(chunk)
                        extracted += len(chunk)
                        if written > info.file_size or extracted > MAX_EXTRACTED_BYTES:
                            raise UpdateError("El contenido del ZIP no coincide con su tamaño declarado.")
                        output.write(chunk)
                        if progress:
                            progress("Preparando archivos", extracted, total)
                if written != info.file_size:
                    raise UpdateError("Un archivo del ZIP está incompleto.")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
        if isinstance(error, UpdateError):
            raise
        raise UpdateError("El ZIP no se pudo verificar o descomprimir.") from error
    return destination / "MiRealmBot"


def validate_staged(staged_root: Path, release: ReleaseInfo) -> None:
    required = ("MiRealmBot.exe", "_internal/python312.dll", "BUILD-MANIFEST.json")
    if not all((staged_root / relative).is_file() for relative in required):
        raise UpdateError("La descarga no contiene todos los archivos del programa.")
    manifest_path = staged_root / "BUILD-MANIFEST.json"
    if manifest_path.stat().st_size > MAX_API_BYTES:
        raise UpdateError("El manifiesto de la descarga supera el tamaño permitido.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise UpdateError("El manifiesto de la descarga no se pudo interpretar.") from error
    if not isinstance(manifest, dict) or manifest.get("application") != "MiRealmBot" or manifest.get("version") != release.version:
        raise UpdateError("La versión incluida en el ZIP no coincide con la Release.")
    config_source = manifest.get("configuration_source")
    if config_source == CONFIG_TEMPLATE_PATH.name:
        required = (CONFIG_TEMPLATE_PATH.name, LEGACY_CONFIG_PATH.name,
                    "_internal/bot_agent.js", "_internal/autonexus_agent.js")
        if (staged_root / DEFAULT_CONFIG_PATH.name).exists():
            raise UpdateError("La Release incluye una configuración personal; no se instalará.")
        if not all((staged_root / relative).is_file() for relative in required):
            raise UpdateError("La descarga no contiene todos los archivos del programa.")
        if ((staged_root / CONFIG_TEMPLATE_PATH.name).read_bytes()
                != (staged_root / LEGACY_CONFIG_PATH.name).read_bytes()):
            raise UpdateError("La configuración de compatibilidad no coincide con el ejemplo público.")
        resources = manifest.get("resource_files")
        if not isinstance(resources, list) or not resources:
            raise UpdateError("El manifiesto no conserva los recursos de la aplicación.")
        for relative in resources:
            if (not isinstance(relative, str) or not relative or "\\" in relative
                    or PurePosixPath(relative).is_absolute()
                    or any(part in ("", ".", "..") or ":" in part for part in relative.split("/"))):
                raise UpdateError("El manifiesto contiene un recurso sin ruta relativa válida.")
            if not (staged_root / "_internal" / relative).is_file():
                raise UpdateError("La descarga no contiene todos los recursos del programa.")
    elif config_source in (None, "autonexus.example.json") and parse_version(release.version) < (0, 4, 0):
        # Older release manifests predate configuration_source. Keep their
        # actual contract without treating an unknown future layout as legacy.
        if not all((staged_root / relative).is_file() for relative in (
                "_internal/autonexus_agent.js", LEGACY_CONFIG_PATH.name)):
            raise UpdateError("La descarga no contiene todos los archivos del programa.")
    else:
        raise UpdateError("La Release no declara un formato de configuración compatible.")


def _portable_root(app_root: Path) -> Path:
    root = Path(app_root).resolve()
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise UpdateError("La actualización automática está disponible en MiRealmBot.exe. Para el código fuente, descarga la nueva versión desde GitHub.")
    if root != APP_ROOT.resolve() or not (root / "MiRealmBot.exe").is_file() or not (root / "_internal").is_dir():
        raise UpdateError("No se reconoció la carpeta de la instalación portátil.")
    return root


def prepare_update(release: ReleaseInfo, app_root: Path = APP_ROOT, *, cancel_event: threading.Event | None = None,
                   progress: Progress | None = None) -> PreparedUpdate:
    root = _portable_root(app_root)
    if parse_version(release.version) <= parse_version(VERSION):
        raise UpdateError("Sólo se pueden instalar versiones más recientes.")
    _cancelled(cancel_event)
    workspace = root.parent / (".MiRealmBot-update-" + uuid4().hex)
    workspace.mkdir()
    try:
        checksum_bytes = _read_small(release.checksum_url, MAX_CHECKSUM_BYTES, cancel_event)
        if len(checksum_bytes) != release.checksum_size:
            raise UpdateError("El archivo SHA256SUMS quedó incompleto.")
        expected_hash = checksum_for_archive(checksum_bytes)
        if release.github_digest and expected_hash != release.github_digest:
            raise UpdateError("La huella de GitHub no coincide con SHA256SUMS.")
        archive_path = workspace / ZIP_NAME
        _download_archive(release, archive_path, expected_hash, cancel_event, progress)
        _cancelled(cancel_event)
        if progress:
            progress("Verificando y preparando", 0, 0)
        staged = extract_archive(archive_path, workspace / "staged", cancel_event=cancel_event, progress=progress)
        validate_staged(staged, release)
        return PreparedUpdate(workspace, staged, root, release)
    except UpdateError:
        # Keep failed downloads as local evidence; no user installation was changed.
        raise
    except (OSError, ValueError) as error:
        raise UpdateError(f"No se pudo preparar la actualización: {error}") from error


def _current_process_ticks() -> int:
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
        raise UpdateError("No se pudo identificar el proceso que debe cerrarse para actualizar.")
    filetime = creation.dwHighDateTime << 32 | creation.dwLowDateTime
    return filetime + 504911232000000000  # Windows FILETIME epoch to .NET DateTime ticks.


def launch_update(prepared: PreparedUpdate) -> Path:
    root = _portable_root(prepared.app_root)
    workspace = prepared.workspace.resolve()
    if (workspace.parent != root.parent or not workspace.name.startswith(".MiRealmBot-update-")
            or prepared.staged_root.resolve() != workspace / "staged" / "MiRealmBot"):
        raise UpdateError("La carpeta preparada no pertenece a esta instalación.")
    validate_staged(prepared.staged_root, prepared.release)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = root.parent / (root.name + ".backup-" + stamp + "-" + uuid4().hex[:8])
    plan = {
        "format": 1, "app_root": str(root), "workspace": str(workspace),
        "staged_root": str(prepared.staged_root.resolve()), "backup_root": str(backup),
        "version": prepared.release.version, "parent_pid": os.getpid(),
        # Keep the process identity as decimal text across the JSON boundary;
        # only the process owner in the worker interprets it as DateTime ticks.
        "parent_started_ticks": str(_current_process_ticks()), "restart": True,
    }
    script = workspace / "updater_worker.ps1"
    plan_path = workspace / "plan.json"
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.is_file():
        raise UpdateError("No se encontró Windows PowerShell para terminar la actualización.")
    if script.exists() or plan_path.exists():
        raise UpdateError("Esta actualización ya fue enviada para instalar.")
    try:
        shutil.copyfile(resource_path("updater_worker.ps1"), script)
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Plan", str(plan_path)],
                         cwd=workspace, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as error:
        # No child was created; retain the verified download but release these
        # two launch inputs so an explicit retry can start the same update.
        for owned_file in (plan_path, script):
            if owned_file.is_file():
                owned_file.unlink()
        raise UpdateError(f"No se pudo iniciar el instalador: {error}") from error
    return plan_path
