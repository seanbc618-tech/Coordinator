"""End-to-end tests for global multi-project Coordinator TUI operation.

Spawns three no-argument ``coordinator`` processes in real PTYs against one
shared Supervisor, completes onboarding, sends goals, detaches/reconnects one
client, and asserts event isolation plus continued execution.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import pty
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import textwrap
import time
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, create_task, get_task, init_db, project_list_events
from local_cli_coordinator.projects import inspect_project, list_projects
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_protocol import PROTOCOL_VERSION, RequestEnvelope
from local_cli_coordinator.supervisor_server import send_request
from local_cli_coordinator.tui_bundle import locate_tui_bundle
from local_cli_coordinator.tui_launcher import build_tui_argv, find_node_executable

from tests.helpers import SRC
from tests.test_tui_pty import (
    _cleanup_tui,
    _drain_pty,
    _final_frame_text,
    _read_available,
    _strip_ansi,
    _type_ctrl_c,
    _type_enter_and_wait,
    _type_string_and_wait,
    _wait_for_connection,
    _wait_for_exit,
    _wait_for_exit_draining,
)

ROOT = Path(__file__).resolve().parents[1]
PROJECT_NAMES = ("proj-a", "proj-b", "proj-c")
GOAL_TEXT = {
    "proj-a": "GOAL_ALPHA build auth",
    "proj-b": "GOAL_BETA add tests",
    "proj-c": "GOAL_GAMMA ship feature",
}
MARKER = "feature.txt"
MARKER_CONTENT = "done"


def _worker_command() -> str:
    body = f"from pathlib import Path; Path('{MARKER}').write_text('{MARKER_CONTENT}')"
    return f'{sys.executable} -c "{body}"'


def _verify_command() -> str:
    return (
        f'{sys.executable} -c "from pathlib import Path; '
        f"assert Path('{MARKER}').read_text() == '{MARKER_CONTENT}'\""
    )


def _resize_pty(fd: int, cols: int, rows: int) -> None:
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def _spawn_tui_argv(argv: list[str], env: dict[str, str], *, cols: int = 100, rows: int = 30) -> tuple[int, int]:
    """Spawn Node with a launcher-built argv in a PTY."""
    master_fd, slave_fd = pty.openpty()
    pid = os.fork()
    if pid == 0:
        os.close(master_fd)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.execvpe(argv[0], argv, env)
        os._exit(1)

    os.close(slave_fd)
    if cols > 0 and rows > 0:
        _resize_pty(master_fd, cols, rows)
        time.sleep(0.1)
    return pid, master_fd


def _spawn_project_tui(
    paths: RuntimePaths,
    repo: Path,
    env: dict[str, str],
    *,
    cols: int = 100,
    rows: int = 30,
) -> tuple[int, int]:
    """Spawn the packaged TUI the same way the no-arg launcher does."""
    node = find_node_executable()
    if node is None:
        raise unittest.SkipTest("node executable not found in PATH")
    located = locate_tui_bundle()
    with located.as_file() as bundle_path:
        argv = build_tui_argv(
            paths=paths,
            bundle_path=bundle_path,
            git_root=repo.resolve(),
            node_executable=node,
        )
    return _spawn_tui_argv(argv, env, cols=cols, rows=rows)


def _wait_for_text(fd: int, needle: str, timeout: float = 30.0) -> str:
    chunks: list[bytes] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.2))
        if ready:
            try:
                data = os.read(fd, 4096)
                if not data:
                    break
                chunks.append(data)
            except OSError:
                break
        text = b"".join(chunks).decode("utf-8", errors="replace")
        if needle in _strip_ansi(text):
            return text
    return b"".join(chunks).decode("utf-8", errors="replace")


def _write_gate_config(home: Path, repos: dict[str, Path]) -> None:
    config_dir = home / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    worker = _worker_command().replace('"', '\\"')
    verify = _verify_command().replace('"', '\\"')
    config_dir.joinpath("agents.toml").write_text(textwrap.dedent(f"""
        [agents.worker]
        command = "{worker}"
        capabilities = ["code"]
        max_concurrency = 2
        role = "worker"
    """).strip(), encoding="utf-8")

    repo_blocks = []
    for name, repo_path in repos.items():
        draft = inspect_project(repo_path)
        repo_blocks.append(textwrap.dedent(f"""
            [repos."{draft.repo_id}"]
            path = "{repo_path}"
            default_branch = "main"
            remote = "origin"
            branch_prefix = "coord/"
            allow_push = false
            merge_policy = "no_push"
            review_policy = "tests_only"
            verify_commands = ["{verify}"]
        """).strip())
    config_dir.joinpath("repos.toml").write_text("\n\n".join(repo_blocks), encoding="utf-8")
    config_dir.joinpath("policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = true
        require_acceptance_criteria = true
        require_verification_commands = true
        require_handoff_summary = false
        max_files_touched = 3
        max_expected_minutes = 30
        max_attempts = 3
        max_tasks_per_run = 1
        max_tasks_per_day = 100
        max_consecutive_failures = 3
        split_if_touches_multiple_subsystems = true
        split_if_research_and_code_are_mixed = true

        [daemon_policy]
        run_discovery_before_tasks = false
    """).strip(), encoding="utf-8")


def _setup_repos(root: Path) -> dict[str, Path]:
    repos: dict[str, Path] = {}
    for name in PROJECT_NAMES:
        repo = root / "repos" / name
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "coordinator@example.local"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Coordinator Test"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text(f"unique seed for {name}\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"initial {name}"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        repos[name] = repo
    return repos


def _seed_tasks(paths: RuntimePaths, repos: dict[str, Path]) -> dict[str, str]:
    conn = connect(paths.database)
    init_db(conn)
    task_ids: dict[str, str] = {}
    try:
        for name, repo_path in repos.items():
            draft = inspect_project(repo_path)
            row = conn.execute(
                "select id from projects where canonical_path = ?",
                (str(repo_path.resolve()),),
            ).fetchone()
            if row is None:
                raise AssertionError(f"project not registered for {name}")
            project_id = row["id"]
            task_ids[name] = create_task(
                conn,
                title=f"e2e-task-{name}",
                repo=draft.repo_id,
                source_path=f"inbox/{name}/e2e.md",
                priority="normal",
                capabilities=["code"],
                goal=f"Create {MARKER} for {name}",
                acceptance_criteria=[f"{MARKER} contains {MARKER_CONTENT}"],
                verification_commands=[],
                project_id=project_id,
            )
    finally:
        conn.close()
    return task_ids


def _wait_tasks_done(paths: RuntimePaths, task_ids: dict[str, str], timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        conn = connect(paths.database)
        try:
            states = {name: get_task(conn, tid)["state"] for name, tid in task_ids.items()}
        finally:
            conn.close()
        if all(state == "done" for state in states.values()):
            return
        time.sleep(0.5)
    raise AssertionError(f"tasks did not complete: {states}")


class GlobalTuiE2ETests(unittest.TestCase):
    """Three-project global Coordinator TUI end-to-end verification."""

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("node") is None:
            raise unittest.SkipTest("node executable not found in PATH")

        cls._tmpdir = tempfile.TemporaryDirectory(prefix="coord-global-e2e-")
        cls.home = Path(cls._tmpdir.name)
        cls.env = os.environ.copy()
        cls.env["PYTHONPATH"] = str(SRC)
        cls.env["COORDINATOR_HOME"] = str(cls.home)
        cls.env["NO_COLOR"] = "1"
        cls.env["TERM"] = "xterm-256color"

        cls.paths = RuntimePaths(
            cls.home / "config",
            cls.home / "data",
            cls.home / "state",
        )
        cls.paths.create()
        cls.repos = _setup_repos(cls.home)
        _write_gate_config(cls.home, cls.repos)

        cls._supervisor = subprocess.Popen(
            [sys.executable, "-m", "local_cli_coordinator", "supervisor", "start", "--foreground"],
            cwd=ROOT,
            env=cls.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if cls._supervisor.poll() is not None:
                stderr = cls._supervisor.stderr.read() if cls._supervisor.stderr else ""
                raise RuntimeError(f"supervisor exited early: {stderr}")
            try:
                resp = send_request(
                    cls.paths.socket,
                    RequestEnvelope(
                        protocol_version=PROTOCOL_VERSION,
                        request_id="e2e-ping",
                        project_id=None,
                        method="system.ping",
                        params={},
                    ),
                )
                if resp.ok:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("supervisor did not become ready")

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run(
            [sys.executable, "-m", "local_cli_coordinator", "supervisor", "stop"],
            cwd=ROOT,
            env=cls.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if cls._supervisor.poll() is None:
            cls._supervisor.terminate()
            try:
                cls._supervisor.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls._supervisor.kill()
        if cls._supervisor.stderr is not None:
            cls._supervisor.stderr.close()
        cls._tmpdir.cleanup()

    def test_three_project_global_tui_operation(self) -> None:
        clients: dict[str, tuple[int, int, str]] = {}
        outputs: dict[str, str] = {}

        try:
            for name in PROJECT_NAMES:
                pid, fd = _spawn_project_tui(self.paths, self.repos[name], self.env)
                clients[name] = (pid, fd, "live")

                onboarding = _wait_for_text(fd, "Register this project?", timeout=45.0)
                self.assertIn(
                    "Register this project?",
                    _strip_ansi(onboarding),
                    f"{name}: onboarding screen missing",
                )
                _drain_pty(fd, quiet_time=0.3)
                _type_enter_and_wait(fd)
                _drain_pty(fd, quiet_time=0.3)
                connected = _wait_for_connection(fd, timeout=45.0)
                self.assertIn("connected", connected, f"{name}: TUI did not connect")
                self.assertIn("proj-", _strip_ansi(connected), f"{name}: registered project id missing")
                self.assertNotIn("__onboarding__", _final_frame_text(connected), f"{name}: still onboarding")
                time.sleep(1.0)
                _drain_pty(fd, quiet_time=0.3)

                goal = GOAL_TEXT[name]
                conn = connect(self.paths.database)
                try:
                    project_id = conn.execute(
                        "select id from projects where canonical_path = ?",
                        (str(self.repos[name].resolve()),),
                    ).fetchone()["id"]
                finally:
                    conn.close()

                chat_resp = send_request(
                    self.paths.socket,
                    RequestEnvelope(
                        protocol_version=PROTOCOL_VERSION,
                        request_id=f"goal-{name}",
                        project_id=project_id,
                        method="chat.send",
                        params={"text": goal},
                    ),
                )
                self.assertTrue(chat_resp.ok, f"{name}: chat.send failed: {chat_resp.error}")
                conn = connect(self.paths.database)
                try:
                    rows = conn.execute(
                        "select payload from supervisor_events "
                        "where project_id = ? and event_type = 'chat.message'",
                        (project_id,),
                    ).fetchall()
                finally:
                    conn.close()
                payloads = [row["payload"] for row in rows]
                self.assertTrue(
                    any(goal in payload for payload in payloads),
                    f"{name}: goal not recorded in supervisor events",
                )
                outputs[name] = connected

            task_ids = _seed_tasks(self.paths, self.repos)

            detach_name = "proj-b"
            detach_pid, detach_fd, _ = clients[detach_name]
            _drain_pty(detach_fd, quiet_time=0.3)
            _type_string_and_wait(detach_fd, "/quit")
            _type_enter_and_wait(detach_fd)
            exit_code = _wait_for_exit_draining(detach_pid, detach_fd, timeout=15.0)
            if exit_code is None:
                _drain_pty(detach_fd, quiet_time=0.2, max_time=1.0)
                _type_ctrl_c(detach_fd)
                exit_code = _wait_for_exit_draining(detach_pid, detach_fd, timeout=10.0)
            try:
                os.close(detach_fd)
            except OSError:
                pass
            self.assertIsNotNone(exit_code, f"{detach_name}: detach did not exit")
            self.assertEqual(exit_code, 0, f"{detach_name}: expected clean detach, got {exit_code}")
            try:
                os.close(detach_fd)
            except OSError:
                pass
            try:
                os.waitpid(detach_pid, 0)
            except ChildProcessError:
                pass
            clients[detach_name] = (detach_pid, detach_fd, "detached")

            _wait_tasks_done(self.paths, task_ids)

            resp = send_request(
                self.paths.socket,
                RequestEnvelope(
                    protocol_version=PROTOCOL_VERSION,
                    request_id="e2e-ping-after-detach",
                    project_id=None,
                    method="system.ping",
                    params={},
                ),
            )
            self.assertTrue(resp.ok, "supervisor unreachable after client detach")

            reconnect_pid, reconnect_fd = _spawn_project_tui(self.paths, self.repos[detach_name], self.env)
            reconnect_output = _wait_for_connection(reconnect_fd, timeout=45.0)
            self.assertIn("connected", reconnect_output, "reconnect did not reach connected state")
            self.assertNotIn(
                "Register this project?",
                _final_frame_text(reconnect_output),
                "reconnect should skip onboarding for registered project",
            )
            clients[detach_name] = (reconnect_pid, reconnect_fd, "reconnected")
            outputs[detach_name] = reconnect_output

            conn = connect(self.paths.database)
            try:
                project_rows = list_projects(conn)
                self.assertEqual(len(project_rows), 3, "expected three registered projects")
                for name in PROJECT_NAMES:
                    project_id = conn.execute(
                        "select id from projects where canonical_path = ?",
                        (str(self.repos[name].resolve()),),
                    ).fetchone()["id"]
                    events = project_list_events(conn, project_id=project_id)
                    self.assertGreater(len(events), 0, f"{name}: no persisted events")
                    for event in events:
                        self.assertEqual(event["project_id"], project_id)
                    for other in PROJECT_NAMES:
                        if other == name:
                            continue
                        other_marker = f"e2e-task-{other}"
                        self.assertNotIn(
                            other_marker,
                            outputs[name],
                            f"{name} leaked activity from {other}",
                        )
            finally:
                conn.close()

            for name in PROJECT_NAMES:
                if name == detach_name:
                    continue
                pid, fd, _ = clients[name]
                extra = _read_available(fd, timeout=1.0)
                outputs[name] += extra

            for name, tid in task_ids.items():
                conn = connect(self.paths.database)
                try:
                    self.assertEqual(get_task(conn, tid)["state"], "done", f"{name} task not done")
                finally:
                    conn.close()

        finally:
            for name, (pid, fd, _) in clients.items():
                if fd >= 0:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    _cleanup_tui(pid, fd)


class NoArgumentCoordinatorTests(unittest.TestCase):
    """Smoke test that no-argument coordinator reaches the packaged TUI."""

    def test_no_argument_coordinator_starts_from_git_repo(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node executable not found in PATH")

        with tempfile.TemporaryDirectory(prefix="coord-noarg-") as tmp:
            home = Path(tmp) / "home"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(SRC)
            env["COORDINATOR_HOME"] = str(home)
            env["NO_COLOR"] = "1"
            env["TERM"] = "xterm-256color"

            paths = RuntimePaths(home / "config", home / "data", home / "state")
            paths.create()
            repos = _setup_repos(home)
            _write_gate_config(home, repos)

            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                [sys.executable, "-m", "local_cli_coordinator"],
                cwd=str(repos["proj-a"]),
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
            os.close(slave_fd)
            try:
                deadline = time.time() + 20.0
                while time.time() < deadline:
                    if proc.poll() is not None:
                        output = _read_available(master_fd, timeout=1.0)
                        self.fail(
                            "no-argument coordinator exited early\n"
                            f"output={output}"
                        )
                    try:
                        resp = send_request(
                            paths.socket,
                            RequestEnvelope(
                                protocol_version=PROTOCOL_VERSION,
                                request_id="noarg-ping",
                                project_id=None,
                                method="system.ping",
                                params={},
                            ),
                        )
                        if resp.ok:
                            break
                    except OSError:
                        pass
                    time.sleep(0.2)
                else:
                    self.fail("no-argument coordinator did not start supervisor")

                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            finally:
                subprocess.run(
                    [sys.executable, "-m", "local_cli_coordinator", "supervisor", "stop"],
                    cwd=ROOT,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                try:
                    os.close(master_fd)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()