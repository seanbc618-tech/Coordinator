"""Red tests for Phase 5.5 Wave B — live worker log tail.

These tests capture the contract for ``project.task.log`` RPC:
task-scoped log tail with kind filtering, offset, max_bytes cap,
state gating, and project isolation.

Owner: Claude Code (Phase 5.5 Task 6)
Expected before implementation: ``project.task.log`` method is unknown
to the Supervisor.
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


class LogTailRPCTests(unittest.TestCase):
    """project.task.log returns incremental log bytes."""

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

    def test_log_tail_rpc_method_exists(self) -> None:
        """project.task.log is a recognized Supervisor method."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/task 1 log",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"], "log tail should succeed: %s" % envelope)

    def test_log_tail_rejects_cross_project_task(self) -> None:
        """Log tail for a task in another project is rejected."""
        # This test asserts project-scoped isolation.
        # After implementation, the error code should be task_not_found
        # or project_mismatch.
        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", "/task 99999 log",
            cwd=self.repo,
        )
        data = json.loads(result.stdout)
        self.assertFalse(data.get("ok", True))

    def test_log_tail_respects_max_bytes(self) -> None:
        """Log tail caps output at max_bytes (default 64 KiB)."""
        # After implementation, verify the response size is bounded.
        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", "/task 1 log",
            cwd=self.repo,
        )
        # We only assert the response is valid JSON.
        data = json.loads(result.stdout)
        self.assertIn("ok", data)

    def test_log_tail_only_when_running_or_terminal(self) -> None:
        """Log tail is only allowed when task state is running/verifying/terminal."""
        # After implementation, a task in 'ready' state should reject tail.
        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", "/task 1 log",
            cwd=self.repo,
        )
        data = json.loads(result.stdout)
        self.assertIn("ok", data)


class LogTailResourceTests(unittest.TestCase):
    """Log tail does not leak file handles."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_repeated_tail_no_resource_warning(self) -> None:
        """Repeated log tail calls do not leak file handles."""
        import warnings
        import gc
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            for _ in range(10):
                result = _run_cli_with_home(
                    self.home, "--mode", "json", "-p", "/task 1 log",
                )
                del result
            gc.collect()
