"""Red tests for Phase 6 autonomous loop E2E integration.

These tests capture the contract for the full autonomous loop flow:
backlog → evaluation → iteration → RPC surfaces.

Owner: Claude Code (Phase 6 Task 0)
Expected before implementation: RPC methods and modules are missing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, transition_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.fixtures.fake_supervisor import FakeSupervisor
from tests.helpers import ROOT, SRC, init_git_repo

_PYTHON = sys.executable


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


def _write_config(config_dir: Path, repo_path: Path) -> None:
    """Write minimal TOML config with autonomy enabled."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents.toml").write_text(textwrap.dedent("""
        [agents.worker]
        command = "true"
        capabilities = ["code"]
        max_concurrency = 1
        role = "worker"
    """).strip())
    (config_dir / "repos.toml").write_text(textwrap.dedent(f"""
        [repos.test-repo]
        path = "{repo_path}"
        default_branch = "main"
        allow_push = false
        merge_policy = "no_push"
        autonomy_enabled = true
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
        wait_when_running = true
        require_evaluation_before_followup = true
        pause_after_consecutive_failures = 3

        [daemon_policy]
        poll_interval_seconds = 5
    """).strip())


class LoopStatusRPCTests(unittest.TestCase):
    """project.loop.status returns autonomous loop state."""

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
            self.conn, "E2E goal", "test", project_id=self.project_id
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

    def test_project_loop_status_is_project_scoped(self) -> None:
        """/loop returns status only for the current project."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/loop",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"])
        self.assertIn("project_id", envelope["result"])
        self.assertEqual(envelope["result"]["project_id"], self.project_id)


class BacklogRPCTests(unittest.TestCase):
    """project.backlog lists backlog items."""

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

    def test_backlog_rpc_returns_list(self) -> None:
        """/backlog returns a list of backlog items."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/backlog",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"])
        self.assertIn("items", envelope["result"])


class EvaluationsRPCTests(unittest.TestCase):
    """project.evaluations lists task evaluations."""

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

    def test_evaluations_rpc_returns_list(self) -> None:
        """/evals returns a list of evaluations."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/evals",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"])
        self.assertIn("evaluations", envelope["result"])


class LoopStepRPCTests(unittest.TestCase):
    """project.loop.step runs one autonomous iteration."""

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
            self.conn, "Step goal", "test", project_id=self.project_id
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

    def test_loop_step_runs_iteration(self) -> None:
        """/loop step runs one bounded autonomous iteration."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/loop step",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"])
        self.assertIn("decision", envelope["result"])
        self.assertIn("reason", envelope["result"])

    def test_loop_step_records_iteration(self) -> None:
        """/loop step persists exactly one loop_iterations row."""
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/loop step",
            cwd=self.repo,
        )
        count = self.conn.execute(
            "select count(*) from loop_iterations where project_id = ?",
            (self.project_id,),
        ).fetchone()[0]
        self.assertEqual(count, 1)
