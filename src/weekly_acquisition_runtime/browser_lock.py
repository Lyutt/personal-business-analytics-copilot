"""Single-holder Browser Acquisition Lock with no queue and no stale cleanup."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import IO, Any

from .errors import BrowserLockOccupied


_PROCESS_GUARD = threading.Lock()
_HELD_PATHS: set[Path] = set()


class BrowserAcquisitionLock:
    """Persistent state file plus an OS byte lock; release never deletes the file."""

    def __init__(self, state_root: Path) -> None:
        self.path = (state_root / "global_browser_acquisition_lock.json").resolve()
        self._handle: IO[bytes] | None = None

    def acquire(self, metadata: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _PROCESS_GUARD:
            if self.path in _HELD_PATHS:
                raise BrowserLockOccupied("Global Browser Acquisition Lock is occupied; queue is disabled")
            try:
                handle = self.path.open("r+b")
            except FileNotFoundError:
                try:
                    handle = self.path.open("x+b")
                except FileExistsError:
                    handle = self.path.open("r+b")
            handle.seek(0)
            existing = handle.read().decode("utf-8", errors="replace").strip()
            if existing:
                try:
                    existing_state = json.loads(existing)
                except json.JSONDecodeError:
                    existing_state = {"status": "invalid"}
                if existing_state.get("status") != "released":
                    handle.close()
                    raise BrowserLockOccupied(
                        "Browser lock metadata is held, stale, or invalid; manual review is required"
                    )
            try:
                self._lock_byte(handle)
            except OSError as exc:
                handle.close()
                raise BrowserLockOccupied("Global Browser Acquisition Lock is occupied; queue is disabled") from exc
            _HELD_PATHS.add(self.path)
            self._handle = handle
        self._write_state({"status": "held", **metadata})

    def release(self) -> None:
        if self._handle is None:
            return
        self._write_state({"status": "released"})
        handle = self._handle
        self._handle = None
        self._unlock_byte(handle)
        handle.close()
        with _PROCESS_GUARD:
            _HELD_PATHS.discard(self.path)

    def _write_state(self, value: dict[str, Any]) -> None:
        assert self._handle is not None
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(payload)
        self._handle.flush()
        os.fsync(self._handle.fileno())

    @staticmethod
    def _lock_byte(handle: IO[bytes]) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            if handle.read(1) == b"":
                handle.write(b" ")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_byte(handle: IO[bytes]) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> "BrowserAcquisitionLock":
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
