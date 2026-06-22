"""Detached Supervisor process lifecycle.

Starts or attaches to the single global Supervisor through the Unix socket API.
Uses argv-based subprocess launch only; no shell profiles, launch agents, cron
entries, or network listeners.
"""

from __future__ import annotations

import os
import signal
import subprocess
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


def _spawn_detached_supervisor(paths: RuntimePaths) -> subprocess.Popen[bytes]:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    log_path = supervisor_log_path(paths)
    with open(log_path, "a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "local_cli_coordinator",
                "supervisor",
                "start",
                "--foreground",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return process


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.terminate()
        except OSError:
            return
    try:
        process.wait(timeout=5.0)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except OSError:
            return
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


def _cleanup_failed_start(
    process: subprocess.Popen[bytes] | None,
    paths: RuntimePaths,
) -> None:
    if process is not None:
        _terminate_process(process)

    paths.socket.unlink(missing_ok=True)

    lock_pid = _read_lock_pid(paths.lock)
    if process is not None and lock_pid == process.pid:
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

    process: subprocess.Popen[bytes] | None = None
    try:
        if ping_supervisor(paths):
            return EnsureSupervisorResult(
                attached=True,
                started=False,
                pid=_read_lock_pid(paths.lock),
            )

        process = _spawn_detached_supervisor(paths)
        try:
            _wait_until_ready(
                paths,
                readiness_timeout=readiness_timeout,
                poll_interval=poll_interval,
            )
        except SupervisorReadinessError:
            _cleanup_failed_start(process, paths)
            raise
        return EnsureSupervisorResult(
            attached=False,
            started=True,
            pid=_read_lock_pid(paths.lock) or process.pid,
        )
    finally:
        if isinstance(lock_result, LockInfo):
            release_lock_at(startup_lock)