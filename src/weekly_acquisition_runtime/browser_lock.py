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
    """Whitelisted metadata plus separate operational state and an OS byte lock."""

    METADATA_ALLOWED = {
        "workflow_run_id",
        "acquisition_attempt_id",
        "adapter_id",
        "acquired_at",
        "process_reference",
    }

    def __init__(self, state_root: Path) -> None:
        self.path = (state_root / "global_browser_acquisition_lock.json").resolve()
        self.operational_path = (
            state_root / "global_browser_acquisition_lock.operational"
        ).resolve()
        self.lock_path = (state_root / "global_browser_acquisition_lock.lck").resolve()
        self._handle: IO[bytes] | None = None

    def acquire(self, metadata: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        unexpected = set(metadata) - self.METADATA_ALLOWED
        if unexpected:
            raise ValueError(f"Browser lock metadata fields are not allowed: {sorted(unexpected)}")
        with _PROCESS_GUARD:
            if self.lock_path in _HELD_PATHS:
                raise BrowserLockOccupied("Global Browser Acquisition Lock is occupied; queue is disabled")
            try:
                handle = self.lock_path.open("r+b")
            except FileNotFoundError:
                try:
                    handle = self.lock_path.open("x+b")
                except FileExistsError:
                    handle = self.lock_path.open("r+b")
            operational_state = (
                self.operational_path.read_text(encoding="utf-8").strip()
                if self.operational_path.exists()
                else ""
            )
            if operational_state not in ("", "released"):
                handle.close()
                raise BrowserLockOccupied(
                    "Browser lock operational state is held, stale, or invalid; manual review is required"
                )
            if not operational_state and self.path.exists():
                handle.close()
                raise BrowserLockOccupied(
                    "Browser lock metadata has no clean release state; manual review is required"
                )
            try:
                self._lock_byte(handle)
            except OSError as exc:
                handle.close()
                raise BrowserLockOccupied("Global Browser Acquisition Lock is occupied; queue is disabled") from exc
            _HELD_PATHS.add(self.lock_path)
            self._handle = handle
        self._write_operational_state("held")
        self._write_metadata(metadata)

    def release(self) -> None:
        if self._handle is None:
            return
        self._write_operational_state("released")
        handle = self._handle
        self._handle = None
        self._unlock_byte(handle)
        handle.close()
        with _PROCESS_GUARD:
            _HELD_PATHS.discard(self.lock_path)

    def _write_operational_state(self, value: str) -> None:
        with self.operational_path.open("w", encoding="ascii", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())

    def _write_metadata(self, value: dict[str, Any]) -> None:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with self.path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

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
