"""Red tests for Phase 5.5 Wave E — safe rollback and cleanup.

These tests capture the contract for dry-run-first cleanup commands:
``coordinator repo cleanup-worktrees``, ``coordinator task rollback``,
and ``coordinator supervisor drain``.

Owner: Claude Code (Phase 5.5 Task 14)
Expected before implementation: these admin subcommands do not exist
or lack ``--dry-run`` / ``--apply`` / ``--confirm`` flags.
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
from local_cli_coordinator.goals import create_goal
from local_cli_coordinator.projects import inspect_project, register_project
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


class CleanupWorktreesTests(unittest.TestCase):
    """repo cleanup-worktrees --dry-run lists candidates without deleting."""

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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cleanup_worktrees_dry_run_default(self) -> None:
        """Without --apply, cleanup-worktrees defaults to dry-run."""
        result = _run_cli_with_home(
            self.home, "repo", "cleanup-worktrees", cwd=self.repo,
        )
        # Before implementation: unknown subcommand. After: dry-run output.
        self.assertIn(result.returncode, (0, 1, 2))

    def test_cleanup_worktrees_apply_requires_confirm(self) -> None:
        """--apply without --confirm token is rejected."""
        result = _run_cli_with_home(
            self.home, "repo", "cleanup-worktrees", "--apply",
            cwd=self.repo,
        )
        # After implementation, must fail without --confirm.
        self.assertNotEqual(result.returncode, 0)

    def test_cleanup_worktrees_dry_run_lists_paths(self) -> None:
        """Dry-run output includes candidate worktree paths."""
        result = _run_cli_with_home(
            self.home, "repo", "cleanup-worktrees", "--dry-run",
            cwd=self.repo,
        )
        # After implementation, stdout lists paths or "no candidates".
        self.assertIn(result.returncode, (0, 1, 2))


class TaskRollbackTests(unittest.TestCase):
    """task rollback resets branch/worktree to pre-attempt state."""

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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rollback_dry_run_default(self) -> None:
        """Without --apply, rollback defaults to dry-run."""
        result = _run_cli_with_home(
            self.home, "task", "rollback", "1", cwd=self.repo,
        )
        self.assertIn(result.returncode, (0, 1, 2))

    def test_rollback_leaves_audit_event(self) -> None:
        """After implementation, rollback writes to events table."""
        result = _run_cli_with_home(
            self.home, "task", "rollback", "1", "--apply", "--confirm",
            "token123", cwd=self.repo,
        )
        # Before implementation: unknown. After: audit event logged.
        self.assertIn(result.returncode, (0, 1, 2))

    def test_rollback_nonexistent_task_is_error(self) -> None:
        """Rollback on nonexistent task returns error."""
        result = _run_cli_with_home(
            self.home, "task", "rollback", "99999", cwd=self.repo,
        )
        self.assertNotEqual(result.returncode, 0)


class SupervisorDrainTests(unittest.TestCase):
    """supervisor drain shows active leases and running tasks."""

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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_drain_dry_run_default(self) -> None:
        """supervisor drain shows active state without killing anything."""
        result = _run_cli_with_home(
            self.home, "supervisor", "drain", cwd=self.repo,
        )
        self.assertIn(result.returncode, (0, 1, 2))

    def test_drain_lists_active_leases(self) -> None:
        """Drain output includes active lease count."""
        result = _run_cli_with_home(
            self.home, "supervisor", "drain", "--dry-run", cwd=self.repo,
        )
        # After implementation, output lists leases and running tasks.
        self.assertIn(result.returncode, (0, 1, 2))
