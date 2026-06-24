"""Launch the Coordinator TUI for the current Git repository."""

from __future__ import annotations

import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .db import connect, init_db
from .global_migration import needs_first_run_migration, prompt_migration_or_exit
from .projects import find_project_by_path
from .runtime_paths import RuntimePaths, resolve_runtime_paths
from .supervisor_identity import INCOMPATIBLE_SUPERVISOR_MESSAGE
from .supervisor_process import (
    SupervisorIncompatibleError,
    SupervisorReadinessError,
    ensure_supervisor,
)
from .tui_bundle import TuiBundleError, locate_tui_bundle

ONBOARDING_PROJECT_ID = "__onboarding__"
FORWARDED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class NotGitRepositoryError(RuntimeError):
    """Raised when the start path is not inside a Git repository."""


def find_node_executable() -> str | None:
    """Return the Node executable path when available."""
    return shutil.which("node")


def resolve_git_root(start: Path | None = None) -> Path:
    """Resolve the canonical Git root for *start* without changing global cwd."""
    cwd = (start or Path.cwd()).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NotGitRepositoryError(f"not a git repository: {cwd}") from exc

    if result.returncode != 0 or not result.stdout.strip():
        raise NotGitRepositoryError(f"not a git repository: {cwd}")

    return Path(result.stdout.strip()).resolve()


def build_tui_argv(
    *,
    paths: RuntimePaths,
    bundle_path: Path,
    git_root: Path,
    node_executable: str,
) -> list[str]:
    """Build the Node argv array for the packaged TUI bundle."""
    locate_tui_bundle()

    conn = connect(paths.database)
    try:
        init_db(conn)
        project = find_project_by_path(conn, git_root)
    finally:
        conn.close()

    argv = [
        node_executable,
        str(bundle_path),
        str(paths.socket),
    ]
    if project is None:
        argv.extend([ONBOARDING_PROJECT_ID, str(git_root)])
    else:
        argv.append(str(project["id"]))
    return argv


def _spawn_tui_process(argv: list[str]) -> subprocess.Popen[bytes]:
    """Start the Node TUI process with inherited stdio."""
    return subprocess.Popen(
        argv,
        stdin=None,
        stdout=None,
        stderr=None,
    )


def _run_tui_with_signal_forwarding(process: subprocess.Popen[bytes]) -> int:
    """Wait for the TUI process and forward terminal signals cleanly."""
    previous_handlers: dict[int, Callable[[int, object], object] | int | None] = {}

    def forward(signum: int, _frame: object) -> None:
        if process.poll() is None:
            process.send_signal(signum)

    for sig in FORWARDED_SIGNALS:
        try:
            previous_handlers[sig] = signal.signal(sig, forward)
        except (ValueError, OSError):
            continue

    try:
        return process.wait()
    finally:
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                continue


def launch_tui(
    *,
    start_path: Path | None = None,
    interactive: bool | None = None,
    input_func: Callable[[str], str] | None = None,
) -> int:
    """Resolve the current Git project and run the packaged Coordinator TUI."""
    try:
        git_root = resolve_git_root(start_path)
    except NotGitRepositoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    node_executable = find_node_executable()
    if node_executable is None:
        print("error: node executable not found in PATH", file=sys.stderr)
        return 1

    paths = resolve_runtime_paths()
    if needs_first_run_migration(paths):
        from .global_migration import detect_legacy_root

        legacy_root = detect_legacy_root(paths)
        if legacy_root is None:
            print("error: legacy migration required but source not found", file=sys.stderr)
            return 1
        if interactive is None:
            interactive = sys.stdin.isatty()
        result = prompt_migration_or_exit(
            legacy_root,
            paths,
            interactive=interactive,
            input_func=input_func,
        )
        if result is None:
            return 1

    try:
        ensure_supervisor(paths)
    except SupervisorIncompatibleError:
        print(f"error: {INCOMPATIBLE_SUPERVISOR_MESSAGE}", file=sys.stderr)
        return 1
    except SupervisorReadinessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        located = locate_tui_bundle()
    except TuiBundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    with located.as_file() as bundle_path:
        argv = build_tui_argv(
            paths=paths,
            bundle_path=bundle_path,
            git_root=git_root,
            node_executable=node_executable,
        )
        process = _spawn_tui_process(argv)
        return _run_tui_with_signal_forwarding(process)