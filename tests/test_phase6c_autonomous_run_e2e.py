"""Red tests for Phase 6C autonomous run RPC integration.

Owner: Grok (Phase 6C Task 0)
Expected before implementation: unsupported RPC methods or missing run payload.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import uuid
from pathlib import Path

from local_cli_coordinator.config import CoordinatorConfig
from local_cli_coordinator.config_runtime import load_config_for_paths
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, transition_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_events import EventBroker
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.supervisor_protocol import PROTOCOL_VERSION, RequestEnvelope
from tests.fixtures.fake_supervisor import FakeSupervisor
from tests.helpers import ROOT, SRC, init_git_repo

_PYTHON = sys.executable


def _write_config(config_dir: Path, repo_path: Path, *, autonomy_enabled: bool = True) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    fake_commander = Path(__file__).resolve().parent / "fixtures" / "fake_commander.py"
    (config_dir / "agents.toml").write_text(textwrap.dedent(f"""
        [agents.worker]
        command = "true"
        capabilities = ["code"]
        max_concurrency = 1
        role = "worker"

        [agents.commander]
        command = "{_PYTHON} {fake_commander}"
        capabilities = ["code", "tests", "docs", "research"]
        max_concurrency = 1
        role = "commander"
    """).strip())
    (config_dir / "repos.toml").write_text(textwrap.dedent(f"""
        [repos.test-repo]
        path = "{repo_path}"
        default_branch = "main"
        allow_push = false
        merge_policy = "no_push"
        autonomy_enabled = {str(autonomy_enabled).lower()}
    """).strip())
    (config_dir / "policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = false
        require_acceptance_criteria = false
        require_verification_commands = false
        require_handoff_summary = false
        max_files_touched = 20
        max_expected_minutes = 60
        max_attempts = 3
        split_if_touches_multiple_subsystems = false
        split_if_research_and_code_are_mixed = false

        [autonomy]
        enabled = true
        max_iterations_per_tick = 1
        max_evaluations_per_iteration = 3
        max_admissions_per_iteration = 1
        max_generated_backlog_per_iteration = 3
        commander_generation_timeout_seconds = 45
        wait_when_running = true
        require_evaluation_before_followup = true
        pause_after_consecutive_failures = 3

        [daemon_policy]
        poll_interval_seconds = 5
    """).strip())


def _run_cli_with_home(
    home: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["COORDINATOR_HOME"] = str(home)
    return subprocess.run(
        [_PYTHON, "-m", "local_cli_coordinator", *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _rpc_envelope(stdout: str) -> dict:
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    return json.loads(lines[0])


class AutonomousRunRpcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()
        self.project_id = self.conn.execute(
            "select id from projects limit 1"
        ).fetchone()["id"]
        self.goal_id = create_goal(
            self.conn, "Run RPC goal", "test", project_id=self.project_id
        )
        transition_goal(self.conn, self.goal_id, "active")
        self.conn.commit()
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(
            broker=EventBroker(),
            config=self.config,
            paths=self.paths,
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _request(self, method: str, params: dict | None = None) -> dict:
        response = self.methods.handle(
            self.conn,
            RequestEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=str(uuid.uuid4()),
                method=method,
                project_id=self.project_id,
                params=params or {},
            ),
        )
        self.assertTrue(response.ok, response.error)
        assert response.result is not None
        return response.result

    def test_loop_start_creates_running_session(self) -> None:
        result = self._request("project.loop.start")
        self.conn.commit()
        run = result.get("run")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run["status"], "running")
        self.assertEqual(result["project_id"], self.project_id)

        row = self.conn.execute(
            "select status from autonomous_run_sessions where project_id = ?",
            (self.project_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "running")

    def test_loop_status_includes_active_run(self) -> None:
        self._request("project.loop.start")
        self.conn.commit()
        result = self._request("project.loop.status")
        run = result.get("run")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run["status"], "running")

    def test_loop_stop_marks_session_stopped(self) -> None:
        self._request("project.loop.start")
        self.conn.commit()
        result = self._request(
            "project.loop.stop", {"reason": "operator stop"}
        )
        self.conn.commit()
        run = result.get("run")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run["status"], "stopped")
        self.assertEqual(run["stop_reason"], "operator stop")

    def test_loop_pause_resume_round_trip(self) -> None:
        self._request("project.loop.start")
        self.conn.commit()
        paused = self._request("project.loop.pause")
        self.assertEqual(paused["run"]["status"], "paused")
        resumed = self._request("project.loop.resume")
        self.assertEqual(resumed["run"]["status"], "running")

    def test_loop_start_rejects_when_autonomy_disabled_without_force(self) -> None:
        disabled_home = self.tmp / "disabled-home"
        disabled_home.mkdir()
        disabled_repo = self.tmp / "disabled-repo"
        disabled_repo.mkdir()
        init_git_repo(disabled_repo)
        disabled_paths = RuntimePaths(
            disabled_home / "config", disabled_home / "data", disabled_home / "state"
        )
        disabled_paths.create()
        _write_config(disabled_home / "config", disabled_repo, autonomy_enabled=False)
        conn = connect(disabled_paths.database)
        init_db(conn)
        draft = inspect_project(disabled_repo)
        register_project(conn, draft, confirmed=True)
        conn.commit()
        project_id = conn.execute("select id from projects limit 1").fetchone()["id"]
        goal_id = create_goal(conn, "Disabled goal", "test", project_id=project_id)
        transition_goal(conn, goal_id, "active")
        conn.commit()
        config = load_config_for_paths(disabled_paths)
        methods = SupervisorMethods(
            broker=EventBroker(),
            config=config,
            paths=disabled_paths,
        )
        response = methods.handle(
            conn,
            RequestEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=str(uuid.uuid4()),
                method="project.loop.start",
                project_id=project_id,
                params={},
            ),
        )
        conn.close()
        self.assertFalse(response.ok)
        self.assertIn("autonomy", (response.error or "").lower())


class AutonomousRunRestartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()
        self.project_id = self.conn.execute(
            "select id from projects limit 1"
        ).fetchone()["id"]
        self.goal_id = create_goal(
            self.conn, "Restart goal", "test", project_id=self.project_id
        )
        transition_goal(self.conn, self.goal_id, "active")
        self.conn.commit()
        self._orig_home = os.environ.get("COORDINATOR_HOME")
        os.environ["COORDINATOR_HOME"] = str(self.home)
        self._processes: list[subprocess.Popen[str]] = []

    def tearDown(self) -> None:
        _run_cli_with_home(self.home, "supervisor", "stop")
        for process in self._processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            self._close_process_streams(process)
        self.conn.close()
        if self._orig_home is not None:
            os.environ["COORDINATOR_HOME"] = self._orig_home
        else:
            os.environ.pop("COORDINATOR_HOME", None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _close_process_streams(self, process: subprocess.Popen[str]) -> None:
        for stream in (process.stdout, process.stderr, process.stdin):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass

    def _wait_for_supervisor(self, timeout: float = 10.0) -> None:
        from local_cli_coordinator.supervisor_process import ping_supervisor

        deadline = time.time() + timeout
        while time.time() < deadline:
            if ping_supervisor(self.paths):
                return
            time.sleep(0.05)
        self.fail("supervisor did not become ready")

    def _start_foreground_supervisor(self) -> subprocess.Popen[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        env["COORDINATOR_HOME"] = str(self.home)
        process = subprocess.Popen(
            [
                _PYTHON,
                "-m",
                "local_cli_coordinator",
                "supervisor",
                "start",
                "--foreground",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._processes.append(process)
        return process

    def test_running_autonomous_session_survives_supervisor_restart(self) -> None:
        process = self._start_foreground_supervisor()

        loop_start = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/loop start", cwd=self.repo,
        )
        self.assertEqual(loop_start.returncode, 0, loop_start.stderr)
        run_id = _rpc_envelope(loop_start.stdout)["result"]["run"]["id"]

        stop = _run_cli_with_home(self.home, "supervisor", "stop")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        process.wait(timeout=10.0)

        self._start_foreground_supervisor()

        conn = connect(self.paths.database)
        init_db(conn)
        row = conn.execute(
            "select id, status from autonomous_run_sessions where id = ?",
            (run_id,),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "running")

        run_status = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/loop run", cwd=self.repo,
        )
        self.assertEqual(run_status.returncode, 0, run_status.stderr)
        envelope = _rpc_envelope(run_status.stdout)
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["result"]["run"]["id"], run_id)
        self.assertEqual(envelope["result"]["run"]["status"], "running")


class AutonomousRunCliRpcTests(unittest.TestCase):
    """CLI --mode rpc maps /loop start|stop|run to Supervisor methods."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()
        self.goal_id = create_goal(
            self.conn, "CLI goal", "test", project_id=self.conn.execute(
                "select id from projects limit 1"
            ).fetchone()["id"]
        )
        transition_goal(self.conn, self.goal_id, "active")
        self.conn.commit()
        self._orig_home = os.environ.get("COORDINATOR_HOME")
        os.environ["COORDINATOR_HOME"] = str(self.home)
        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.conn.close()
        if self._orig_home is not None:
            os.environ["COORDINATOR_HOME"] = self._orig_home
        else:
            os.environ.pop("COORDINATOR_HOME", None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
