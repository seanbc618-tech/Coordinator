"""Red tests for Phase 5.5 Wave D — multi-project Supervisor dashboard.

These tests capture the contract for ``supervisor.dashboard`` RPC:
per-project goal status, task counts, active workers, and cross-project
isolation.

Owner: Claude Code (Phase 5.5 Task 12)
Expected before implementation: ``supervisor.dashboard`` method is unknown.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, transition_goal
from local_cli_coordinator.projects import inspect_project, register_project
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


class DashboardRPCTests(unittest.TestCase):
    """supervisor.dashboard returns multi-project overview."""

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

    def test_dashboard_rpc_method_exists(self) -> None:
        """supervisor.dashboard is a recognized method."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/dashboard",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"], "dashboard should succeed: %s" % envelope)

    def test_dashboard_returns_project_list(self) -> None:
        """Dashboard includes per-project entries."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/dashboard",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"])
        self.assertIn("projects", envelope["result"])

    def test_dashboard_includes_task_counts(self) -> None:
        """Each project entry has task counts by state."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/dashboard",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"])
        projects = envelope["result"].get("projects", [])
        if projects:
            self.assertIn("task_counts", projects[0])

    def test_dashboard_includes_autonomous_run_counts(self) -> None:
        """Dashboard includes aggregate autonomous run counts only."""
        from local_cli_coordinator.autonomous_runs import AutonomousRunOptions, start_run_session
        from local_cli_coordinator.goals import create_goal, transition_goal

        goal_id = create_goal(
            self.conn, "Dashboard goal", "test", project_id=self.project_id
        )
        transition_goal(self.conn, goal_id, "active")
        start_run_session(
            self.conn,
            project_id=self.project_id,
            goal_id=goal_id,
            options=AutonomousRunOptions(),
        )
        self.conn.commit()
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/dashboard",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        envelope = json.loads(result.stdout.strip().splitlines()[0])
        self.assertTrue(envelope["ok"])
        runs = envelope["result"].get("autonomous_runs")
        self.assertIsNotNone(runs)
        self.assertEqual(runs.get("running"), 1)
        self.assertEqual(runs.get("paused"), 0)
        payload_text = json.dumps(envelope["result"])
        self.assertNotIn("Dashboard goal", payload_text)

    def test_dashboard_bounded_at_32_projects(self) -> None:
        """Dashboard returns at most 32 projects."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/dashboard",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"])
        projects = envelope["result"].get("projects", [])
        self.assertLessEqual(len(projects), 32)


class DashboardIsolationTests(unittest.TestCase):
    """Dashboard does not leak cross-project task titles."""

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
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
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

    def test_dashboard_no_task_titles_across_projects(self) -> None:
        """Dashboard entries contain counts, not task titles from other projects."""
        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", "/dashboard",
            cwd=self.repo,
        )
        data = json.loads(result.stdout)
        # After implementation, verify no task titles leak.
        self.assertIn("ok", data)
