"""Cross-platform locks for run and repository ownership."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Any

from .errors import WorkspaceError
from .paths import canonical_path


def repository_lock_key(git_common_dir: Path) -> str:
    identity = os.path.normcase(str(canonical_path(git_common_dir))).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


class FileLock(AbstractContextManager["FileLock"]):
    def __init__(self, path: Path, owner: dict[str, Any]):
        self.path = canonical_path(path)
        self.owner = dict(owner)
        self._descriptor: int | None = None

    def acquire(self) -> "FileLock":
        if self._descriptor is not None:
            raise WorkspaceError(f"Lock is already held by this instance: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            self._acquire_windows_lock()
        else:
            self._acquire_flock()
        return self

    def release(self) -> None:
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        if os.name == "nt":
            import msvcrt

            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(descriptor)
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    def _write_owner(self, descriptor: int) -> None:
        payload = json.dumps(self.owner, ensure_ascii=False).encode("utf-8")
        os.ftruncate(descriptor, 0)
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)

    def _acquire_flock(self) -> None:
        import fcntl

        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            raise WorkspaceError(self._owned_message()) from exc
        self._write_owner(descriptor)
        self._descriptor = descriptor

    def _acquire_windows_lock(self) -> None:
        import msvcrt

        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            os.close(descriptor)
            raise WorkspaceError(self._owned_message()) from exc
        self._write_owner(descriptor)
        self._descriptor = descriptor

    def _read_owner(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _owned_message(self, owner: dict[str, Any] | None = None) -> str:
        current = owner if owner is not None else self._read_owner()
        return (
            f"Lock is already owned: {self.path}; "
            f"run_id={current.get('run_id', 'unknown')}; pid={current.get('pid', 'unknown')}"
        )


class LockSet(AbstractContextManager["LockSet"]):
    """Acquire run ownership before repository ownership to avoid deadlocks."""

    def __init__(self, run_lock: FileLock, repository_lock: FileLock):
        self.run_lock = run_lock
        self.repository_lock = repository_lock
        self._entered = False

    def __enter__(self) -> "LockSet":
        if self._entered:
            raise WorkspaceError("Lock set is already held")
        self.run_lock.acquire()
        try:
            self.repository_lock.acquire()
        except BaseException:
            self.run_lock.release()
            raise
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._entered:
            return
        self.repository_lock.release()
        self.run_lock.release()
        self._entered = False
