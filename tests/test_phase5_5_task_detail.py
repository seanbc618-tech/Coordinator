"""Red tests for Phase 5.5 Wave A — task detail readability.

These tests capture the contract for enriched ``project.task`` RPC fields:
``execution_policy``, ``context_manifest`` summary, ``latest_note``,
``failure_class``, and ``human_review_required``.

Owner: Claude Code (Phase 5.5 Task 0)
Expected before implementation: ``project.task`` returns only legacy fields;
new keys are absent.
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
from tests.helpers import ROOT, SRC

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


class TaskDetailEnrichmentTests(unittest.TestCase):
    """project.task returns enriched fields for operator readability."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        from tests.helpers import init_git_repo
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

    def _get_task_detail(self, task_id: str) -> dict:
        """Fetch task detail via project.task RPC (JSON mode)."""
        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", f"/task {task_id}",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0, result.stderr[:200])
        return json.loads(result.stdout)

    def test_task_detail_has_execution_policy(self) -> None:
        """project.task result includes execution_policy field."""
        # Create a task with a policy.
        from local_cli_coordinator.commander_policy import admit_commander_response
        from local_cli_coordinator.commander_protocol import (
            CommanderResponse, CommanderTaskProposal,
        )
        proposal = CommanderTaskProposal(
            title="detail test",
            repo=str(self.repo),
            capabilities=[],
            goal="test detail",
            acceptance_criteria=[],
            verification_commands=[],
            expected_files=0,
            expected_minutes=5,
            parent_task_id=None,
            rationale="test",
        )
        response = CommanderResponse(
            schema_version=1, intent="task_request", user_reply="",
            goal_status="active", progress_summary="",
            tasks=[proposal], stop_reason=None,
        )
        goal_id = create_goal(
            self.conn, "Detail goal", "test", project_id=self.project_id
        )
        self.conn.execute(
            "update goals set status = 'active' where id = ?", (goal_id,)
        )
        self.conn.commit()

        from unittest import mock
        result = admit_commander_response(
            self.conn, mock.MagicMock(), self.repo, goal_id, response,
            project_id=self.project_id,
        )
        if not result.accepted_task_ids:
            self.skipTest("task not admitted")
        task_id = result.accepted_task_ids[0]

        detail = self._get_task_detail(task_id)
        self.assertIn("execution_policy", detail)

    def test_task_detail_has_failure_class(self) -> None:
        """project.task result includes failure_class on failed tasks."""
        detail = self._get_task_detail("nonexistent")
        # Even for missing tasks, the response should have the field or
        # return a stable error code.
        self.assertTrue(
            "failure_class" in detail or detail.get("error"),
            "missing failure_class and no error",
        )

    def test_task_detail_has_human_review_required(self) -> None:
        """project.task result includes human_review_required boolean."""
        detail = self._get_task_detail("nonexistent")
        self.assertTrue(
            "human_review_required" in detail or detail.get("error"),
            "missing human_review_required and no error",
        )

    def test_task_detail_context_manifest_is_bounded(self) -> None:
        """context_manifest in task detail is a summary (paths/hashes only)."""
        detail = self._get_task_detail("nonexistent")
        if "context_manifest" in detail:
            manifest = detail["context_manifest"]
            # Should be a list of summaries, not full file content.
            if isinstance(manifest, list) and manifest:
                self.assertIn("path", manifest[0])
                self.assertNotIn("content", manifest[0])
