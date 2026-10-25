"""A bounded, shared recent-history JSONL store for live sessions.

Only the three MiRealmBot session names and exact historical runtime names
belong to this owner. Other analysis evidence is untouched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, BinaryIO

MAX_FILE_BYTES = 1024 * 1024
BACKUP_COUNT = 2
MAX_RECORD_BYTES = 64 * 1024
TOTAL_LOG_BYTES = MAX_FILE_BYTES * (BACKUP_COUNT + 1)
MAX_RECORD_DEPTH = 32
LOG_BASENAME = "mirealmbot-session"
LEGACY_LOG_FILES = frozenset(("live-autonexus.jsonl", "live-autonexus.1.jsonl", "live-autonexus.2.jsonl"))
_LEGACY_NAME = re.compile(
    r"live-autonexus-\d{8}T\d{6}\.\d{6}Z-pid([1-9]\d*)-[0-9a-f]{32}\.jsonl")


class _EntryLimit(Exception):
    pass


def _preflight(value: Any, remaining: int, depth: int = 0) -> int:
    """Reject giant strings/containers before JSON creates escaped copies.

    This is a lower bound, not a byte-size claim. The encoder below enforces
    the actual UTF-8 bound, including escapes and the final newline.
    """
    if depth > MAX_RECORD_DEPTH or remaining < 1:
        raise _EntryLimit()
    if isinstance(value, str):
        size = len(value) + 2
        if size > remaining:
            raise _EntryLimit()
        return remaining - size
    if isinstance(value, (dict, list, tuple)):
        if len(value) > remaining:
            raise _EntryLimit()
        remaining -= 2
        pairs = value.items() if isinstance(value, dict) else ((None, item) for item in value)
        for key, item in pairs:
            if key is not None:
                remaining = _preflight(key, remaining - 1, depth + 1)
            remaining = _preflight(item, remaining - 1, depth + 1)
        return remaining
    # A numeric scalar cannot encode more cheaply than one byte. Very large
    # integers are rejected before decimal conversion can allocate a copy.
    if isinstance(value, int) and value.bit_length() > remaining * 3:
        raise _EntryLimit()
    return remaining - 1


def _encode_line(entry: dict[str, Any]) -> bytes:
    try:
        _preflight(entry, MAX_RECORD_BYTES - 1)
        encoded = bytearray()
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        for chunk in encoder.iterencode(entry):
            chunk_bytes = chunk.encode("utf-8")
            if len(encoded) + len(chunk_bytes) + 1 > MAX_RECORD_BYTES:
                raise _EntryLimit()
            encoded.extend(chunk_bytes)
        encoded.extend(b"\n")
        return bytes(encoded)
    except _EntryLimit:
        # The full payload still reaches the live callback; only disk history
        # omits it. Keep the omission explicit rather than truncating JSON.
        original_event = entry.get("event")
        marker = {"event": "log_entry_omitted", "reason": "entry_exceeds_log_limits",
                  "max_record_bytes": MAX_RECORD_BYTES}
        if isinstance(original_event, str):
            marker["original_event"] = original_event[:256]
        captured = entry.get("captured_at_utc")
        if isinstance(captured, str):
            marker["captured_at_utc"] = captured[:80]
        pid = entry.get("pid")
        if type(pid) is int and 0 < pid <= 0xFFFFFFFF:
            marker["pid"] = pid
        return (json.dumps(marker, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class BoundedRuntimeLog:
    """Append recent complete records, rotating before the one-MiB boundary.

    A process-lifetime byte lock excludes simultaneous writers in one install.
    Failure to acquire it leaves history unchanged and may be handled without
    disabling the bot. The lock file is one byte; JSONL history is at most
    TOTAL_LOG_BYTES. Historical files that Windows reports as busy/undeletable
    are kept until a later startup, so they are outside that new-store quota.
    """

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / f"{LOG_BASENAME}.jsonl"
        self._handle: BinaryIO | None = None
        self._lock: BinaryIO | None = None
        self._size = 0
        self.legacy_files_removed = 0
        self.legacy_files_retained = 0
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self._lock = (self.directory / f"{LOG_BASENAME}.lock").open("a+b", buffering=0)
            self._lock.seek(0, os.SEEK_END)
            if self._lock.tell() == 0:
                self._lock.write(b"\0")
            self._lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._remove_inactive_legacy()
            for index in range(BACKUP_COUNT + 1):
                self._repair_tail(self._path(index))
            self._handle = self.path.open("a+b", buffering=0)
            self._size = self._handle.tell()
        except Exception:
            self.close()
            raise

    def _path(self, index: int) -> Path:
        return self.path if index == 0 else self.path.with_name(f"{LOG_BASENAME}.{index}.jsonl")

    def _remove_inactive_legacy(self) -> None:
        for path in self.directory.iterdir():
            legacy = path.name in LEGACY_LOG_FILES or _LEGACY_NAME.fullmatch(path.name) is not None
            if not legacy or not path.is_file() or path.is_symlink():
                continue
            try:
                # A legacy Python writer on Windows holds a CRT file handle
                # without FILE_SHARE_DELETE, so an active log cannot be removed.
                # Its filename's PID is the GAME's PID, not proof of a writer.
                path.unlink()
                self.legacy_files_removed += 1
            except OSError:
                # A locked file is retained; retry migration on the next start.
                self.legacy_files_retained += 1

    @staticmethod
    def _repair_tail(path: Path) -> None:
        if not path.exists():
            return
        with path.open("r+b", buffering=0) as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - MAX_FILE_BYTES)
            handle.seek(start)
            tail = handle.read(MAX_FILE_BYTES)
            if start:
                # The first byte may be inside a record or UTF-8 character.
                boundary = tail.find(b"\n")
                tail = tail[boundary + 1:] if boundary >= 0 else b""
            if tail and not tail.endswith(b"\n"):
                boundary = tail.rfind(b"\n")
                tail = tail[:boundary + 1] if boundary >= 0 else b""
            if start:
                handle.seek(0)
                handle.write(tail)
                handle.truncate(len(tail))
            elif len(tail) != size:
                handle.truncate(len(tail))

    def _rotate(self) -> None:
        self._handle.close()
        self._handle = None
        self._path(BACKUP_COUNT).unlink(missing_ok=True)
        for index in range(BACKUP_COUNT - 1, -1, -1):
            previous = self._path(index)
            if previous.exists():
                previous.replace(self._path(index + 1))
        self._handle = self.path.open("a+b", buffering=0)
        self._size = 0

    def record(self, entry: dict[str, Any]) -> None:
        if self._handle is None:
            raise OSError("El registro está cerrado")
        line = _encode_line(entry)
        if self._size + len(line) > MAX_FILE_BYTES:
            self._rotate()
        start = self._size
        try:
            written = self._handle.write(line)
            if written != len(line):
                raise OSError("Escritura incompleta del registro")
            self._size += written
        except Exception:
            # Roll back a partial write to the previous complete-line boundary.
            # If disk failure also prevents rollback, next startup repairs tail.
            try:
                self._handle.truncate(start)
                self._handle.seek(start)
            except OSError:
                pass
            raise

    def close(self) -> None:
        handle, self._handle = self._handle, None
        lock, self._lock = self._lock, None
        try:
            if handle is not None:
                handle.close()
        finally:
            if lock is not None:
                lock.close()  # OS releases its byte lock even after a crash.
