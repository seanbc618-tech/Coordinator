"""Track active worker subprocesses for cooperative cancel."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import MutableMapping

_DEFAULT_GRACE_SECONDS = 5.0


class WorkerRegistry:
    """Thread-safe registry of task_id → running subprocess leader."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    def register(self, task_id: str, process: subprocess.Popen[bytes]) -> None:
        if not task_id:
            return
        with self._lock:
            self._processes[task_id] = process

    def unregister(self, task_id: str) -> None:
        if not task_id:
            return
        with self._lock:
            self._processes.pop(task_id, None)

    def get(self, task_id: str) -> subprocess.Popen[bytes] | None:
        with self._lock:
            return self._processes.get(task_id)

    def active_count(self) -> int:
        """Return the number of in-process worker subprocesses."""
        with self._lock:
            return len(self._processes)

    def terminate(
        self,
        task_id: str,
        *,
        grace_seconds: float = _DEFAULT_GRACE_SECONDS,
    ) -> bool:
        """Signal worker process group; return True if a process was targeted."""
        with self._lock:
            process = self._processes.get(task_id)
        if process is None:
            return False
        self._signal_process_group(process, signal.SIGTERM)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return True
            time.sleep(0.1)
        self._signal_process_group(process, signal.SIGKILL)
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        return True

    @staticmethod
    def _signal_process_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, sig)
                return
            except (ProcessLookupError, PermissionError):
                pass
        try:
            process.send_signal(sig)
        except ProcessLookupError:
            pass


GLOBAL_WORKER_REGISTRY = WorkerRegistry()