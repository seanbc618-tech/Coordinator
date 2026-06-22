"""Detached Supervisor process lifecycle.

Starts or attaches to the single global Supervisor through the Unix socket API.
Uses argv-based subprocess launch only; no shell profiles, launch agents, cron
entries, or network listeners.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from local_cli_coordinator.locks import LockInfo, acquire_lock_at, release_lock_at
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_protocol import PROTOCOL_VERSION, RequestEnvelope
from local_cli_coordinator.supervisor_server import SupervisorTransportError, send_request

DEFAULT_READINESS_TIMEOUT = 30.0
DEFAULT_POLL_INTERVAL = 0.1
STARTUP_LOCK_NAME = "supervisor-startup.lock"
SUPERVISOR_LOG_NAME = "supervisor.log"


class SupervisorProcessError(RuntimeError):
    """Raised when Supervisor process management fails."""


class SupervisorReadinessError(SupervisorProcessError):
    """Raised when a Supervisor fails to become ready in time."""


@dataclass(frozen=True)
class EnsureSupervisorResult:
    attached: bool
    started: bool
    pid: int | None = None


def startup_lock_path(paths: RuntimePaths) -> Path:
    return paths.state_dir / STARTUP_LOCK_NAME


def supervisor_log_path(paths: RuntimePaths) -> Path:
    return paths.state_dir / SUPERVISOR_LOG_NAME


def _ping_request(request_id: str) -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id,
        project_id=None,
        method="system.ping",
        params={},
    )


def ping_supervisor(
    paths: RuntimePaths,
    *,
    timeout: float = 2.0,
) -> bool:
    """Return True when the Supervisor responds to system.ping."""
    if not paths.socket.exists():
        return False
    try:
        response = send_request(paths.socket, _ping_request("ping"), timeout=timeout)
    except SupervisorTransportError:
        return False
    return bool(response.ok and response.result and response.result.get("pong") is True)


def _read_lock_pid(lock_path: Path) -> int | None:
    if not lock_path.exists():
        return None
    try:
        import json

        data = json.loads(lock_path.read_text(encoding="utf-8"))
        return int(data["pid"])
    except (OSError, TypeError, ValueError, KeyError):
        return None


def supervisor_spawn_argv() -> list[str]:
    """Argv used to launch the detached foreground Supervisor."""
    return [
        sys.executable,
        "-m",
        "local_cli_coordinator",
        "supervisor",
        "start",
        "--foreground",
    ]


def _spawn_detached_supervisor(paths: RuntimePaths) -> int:
    """Fork/exec a detached Supervisor without retaining a Popen wrapper."""
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    log_path = supervisor_log_path(paths)
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    argv = supervisor_spawn_argv()
    env = os.environ.copy()

    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
            os.dup2(log_fd, 1)
            os.dup2(log_fd, 2)
            if log_fd > 2:
                os.close(log_fd)
            os.execvpe(argv[0], argv, env)
        except OSError:
            os._exit(1)
        os._exit(1)

    os.close(log_fd)
    if pid < 0:
        raise SupervisorProcessError("failed to fork detached supervisor")
    return pid


def _terminate_pid(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            waited_pid, _status = os.waitpid(pid, os.WNOHANG)
            if waited_pid != 0:
                return
        except ChildProcessError:
            return
        time.sleep(0.05)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def _cleanup_failed_start(
    child_pid: int | None,
    paths: RuntimePaths,
) -> None:
    if child_pid is not None:
        _terminate_pid(child_pid)

    paths.socket.unlink(missing_ok=True)

    lock_pid = _read_lock_pid(paths.lock)
    if child_pid is not None and lock_pid == child_pid:
        release_lock_at(paths.lock)


def _wait_until_ready(
    paths: RuntimePaths,
    *,
    readiness_timeout: float,
    poll_interval: float,
) -> None:
    deadline = time.time() + readiness_timeout
    while time.time() < deadline:
        if ping_supervisor(paths, timeout=min(poll_interval * 2, 2.0)):
            return
        time.sleep(poll_interval)
    raise SupervisorReadinessError(
        f"supervisor did not become ready within {readiness_timeout:.1f}s"
    )


def _wait_for_peer_startup(
    paths: RuntimePaths,
    *,
    readiness_timeout: float,
    poll_interval: float,
) -> EnsureSupervisorResult:
    try:
        _wait_until_ready(
            paths,
            readiness_timeout=readiness_timeout,
            poll_interval=poll_interval,
        )
    except SupervisorReadinessError as exc:
        raise SupervisorReadinessError(
            "supervisor did not become ready while waiting for peer startup"
        ) from exc
    return EnsureSupervisorResult(attached=True, started=False, pid=_read_lock_pid(paths.lock))


def ensure_supervisor(
    paths: RuntimePaths,
    *,
    readiness_timeout: float = DEFAULT_READINESS_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> EnsureSupervisorResult:
    """Attach to a running Supervisor or start one detached process."""
    paths.create()

    if ping_supervisor(paths):
        return EnsureSupervisorResult(
            attached=True,
            started=False,
            pid=_read_lock_pid(paths.lock),
        )

    startup_lock = startup_lock_path(paths)
    lock_result = acquire_lock_at(startup_lock)
    if isinstance(lock_result, str):
        return _wait_for_peer_startup(
            paths,
            readiness_timeout=readiness_timeout,
            poll_interval=poll_interval,
        )

    child_pid: int | None = None
    try:
        if ping_supervisor(paths):
            return EnsureSupervisorResult(
                attached=True,
                started=False,
                pid=_read_lock_pid(paths.lock),
            )

        child_pid = _spawn_detached_supervisor(paths)
        try:
            _wait_until_ready(
                paths,
                readiness_timeout=readiness_timeout,
                poll_interval=poll_interval,
            )
        except SupervisorReadinessError:
            _cleanup_failed_start(child_pid, paths)
            raise
        return EnsureSupervisorResult(
            attached=False,
            started=True,
            pid=_read_lock_pid(paths.lock) or child_pid,
        )
    finally:
        if isinstance(lock_result, LockInfo):
            release_lock_at(startup_lock)