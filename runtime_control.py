"""Coalesce immutable configuration snapshots between the UI and its worker."""
from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot_runtime import RuntimeConfig


class RuntimeControl:
    def __init__(self) -> None:
        self._lock = Lock()
        self._revision = 0
        self._pending: tuple[int, RuntimeConfig] | None = None

    def request(self, config: RuntimeConfig) -> int:
        """Replace any pending snapshot, returning this request's revision."""
        with self._lock:
            self._revision += 1
            self._pending = (self._revision, config)
            return self._revision

    def take_latest(self) -> tuple[int, RuntimeConfig] | None:
        """Consume the latest snapshot once; storage stays bounded to one."""
        with self._lock:
            pending, self._pending = self._pending, None
            return pending
