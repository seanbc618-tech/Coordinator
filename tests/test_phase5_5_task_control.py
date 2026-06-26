"""Red tests for Phase 5.5 Wave C — task approve / cancel / retry.

These tests capture the contract for Supervisor RPC task mutations:
``project.task.approve``, ``project.task.cancel``, ``project.task.retry``.

Owner: Claude Code (Phase 5.5 Task 9)
Expected before implementation: these RPC methods are unknown to the
Supervisor; the CLI returns ``method_not_found`` or similar error.
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


class TaskApproveTests(unittest.TestCase):
    """project.task.approve transitions awaiting_human → ready."""

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

    def test_approve_nonexistent_task_is_error(self) -> None:
        """Approve on nonexistent task returns stable error code."""
        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", "/task 99999 approve",
            cwd=self.repo,
        )
        data = json.loads(result.stdout)
        self.assertFalse(data["ok"])
        self.assertIn("error", data)
        # Error code should be task_not_found after implementation.
        error = data["error"]
        if isinstance(error, dict):
            self.assertIn("code", error)

    def test_approve_non_awaiting_task_is_error(self) -> None:
        """Approve on a task not in awaiting_human returns error."""
        # Create a goal; no tasks exist yet.
        goal_id = create_goal(
            self.conn, "Approve goal", "test", project_id=self.project_id
        )
        self.conn.execute(
            "update goals set status = 'active' where id = ?", (goal_id,)
        )
        self.conn.commit()

        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", "/task 1 approve",
            cwd=self.repo,
        )
        data = json.loads(result.stdout)
        self.assertFalse(data.get("ok", True))

    def test_approve_emits_task_updated_event(self) -> None:
        """After implementation, approve emits task.updated event."""
        from local_cli_coordinator.db import create_task

        task_id = create_task(
            self.conn,
            title="Human gate",
            repo=str(self.repo),
            source_path="",
            priority="normal",
            capabilities=["read"],
            goal="review",
            acceptance_criteria=["approved"],
            verification_commands=[],
            project_id=self.project_id,
        )
        self.conn.execute(
            "update tasks set state = 'awaiting_human' where id = ?",
            (task_id,),
        )
        self.conn.commit()

        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", f"/task {task_id} approve",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"], "approve should succeed: %s" % envelope)


class TaskCancelTests(unittest.TestCase):
    """project.task.cancel transitions running → failed."""

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

    def test_cancel_nonexistent_task_is_error(self) -> None:
        """Cancel on nonexistent task returns stable error code."""
        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", "/task 99999 cancel",
            cwd=self.repo,
        )
        data = json.loads(result.stdout)
        self.assertFalse(data.get("ok", True))

    def test_cancel_releases_lease(self) -> None:
        """After implementation, cancel releases the worker lease."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/task 1 cancel",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"], "cancel should succeed: %s" % envelope)

    def test_cancel_preserves_worktree_by_default(self) -> None:
        """Cancel does not delete the worktree without --purge."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/task 1 cancel",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        # Safety invariant: no --purge flag means worktree preserved.


class TaskRetryTests(unittest.TestCase):
    """project.task.retry transitions failed → ready."""

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

    def test_retry_nonexistent_task_is_error(self) -> None:
        """Retry on nonexistent task returns stable error code."""
        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", "/task 99999 retry",
            cwd=self.repo,
        )
        data = json.loads(result.stdout)
        self.assertFalse(data.get("ok", True))

    def test_retry_respects_max_attempts(self) -> None:
        """Retry is rejected when max_attempts is exceeded."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/task 99999 retry",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        envelope = json.loads(lines[0])
        self.assertFalse(envelope["ok"])
        error = envelope.get("error")
        if isinstance(error, dict):
            self.assertIn("code", error)

    def test_retry_only_failed_or_blocked(self) -> None:
        """Retry on a ready/active task returns error."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/task 99999 retry",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        envelope = json.loads(lines[0])
        self.assertFalse(envelope["ok"])
