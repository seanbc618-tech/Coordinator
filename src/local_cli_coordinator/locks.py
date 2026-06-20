"""Single-instance lockfile guard for the coordinator daemon.

Prevents two daemon processes from operating on the same ledger concurrently.
The lockfile stores the PID of the holding process so stale locks can be
detected and cleaned up.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LOCKFILE_NAME = "coordinator.lock"


@dataclass(frozen=True)
class LockInfo:
    pid: int
    acquired_at: str


def lockfile_path(root: Path) -> Path:
    """Return the path to the coordinator lockfile."""
    return root / "state" / LOCKFILE_NAME


def _write_lock_atomically(lock_path: Path, info: LockInfo) -> None:
    payload = (
        json.dumps({"pid": info.pid, "acquired_at": info.acquired_at}, indent=2)
        + "\n"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(str(lock_path), flags, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)


def acquire_lock_at(lock_path: Path, *, force: bool = False) -> LockInfo | str:
    """Attempt to acquire a lock at an explicit path."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if force:
        lock_path.unlink(missing_ok=True)

    info = LockInfo(
        pid=os.getpid(),
        acquired_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        _write_lock_atomically(lock_path, info)
        return info
    except FileExistsError:
        existing = _read_lock(lock_path)
        if existing is not None and _is_stale(existing.pid):
            lock_path.unlink(missing_ok=True)
            try:
                _write_lock_atomically(lock_path, info)
                return info
            except FileExistsError:
                existing = _read_lock(lock_path)

        if existing is not None:
            return (
                f"daemon is already running (pid {existing.pid}, "
                f"acquired at {existing.acquired_at}). "
                f"Use --force-lock to override."
            )
        return (
            "daemon is already running (lock file exists). "
            "Use --force-lock to override."
        )


def release_lock_at(lock_path: Path) -> bool:
    """Release a lock at an explicit path when held by the current process."""
    existing = _read_lock(lock_path)
    if existing is None:
        return False
    if existing.pid != os.getpid():
        return False
    try:
        lock_path.unlink()
        return True
    except OSError:
        return False


def _read_lock(path: Path) -> LockInfo | None:
    """Read and return lock info from an existing lockfile, or None."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LockInfo(pid=int(data["pid"]), acquired_at=str(data["acquired_at"]))
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None


def _is_stale(pid: int) -> bool:
    """Check whether a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return False  # process exists
    except ProcessLookupError:
        return True  # process is gone
    except PermissionError:
        # Process exists but we can't signal it — treat as alive
        return False


def acquire_lock(root: Path, *, force: bool = False) -> LockInfo | str:
    """Attempt to acquire the daemon lock.

    Returns a :class:`LockInfo` on success, or a human-readable error string
    on failure.  If *force* is True, a stale or existing lock is overwritten.
    """
    return acquire_lock_at(lockfile_path(root), force=force)


def release_lock(root: Path) -> bool:
    """Release the daemon lock if the current process holds it.

    Returns True if the lock was removed, False if it didn't exist or was
    held by a different process.
    """
    return release_lock_at(lockfile_path(root))