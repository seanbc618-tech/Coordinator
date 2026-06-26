"""Red tests for Phase 5.5 Wave A — 总管对话 persona enrichment.

These tests capture the contract for structured orchestration metadata in
``chat.send`` responses and JSON/RPC output: ``next_action``,
``admitted_summary``, ``rejection_reasons``, and ``blocking_reasons``.

Owner: Claude Code (Phase 5.5 Task 0)
Expected before implementation: the ``orchestration`` key is absent from
``chat.send`` results and JSON output.
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


class ChatPersonaOrchestrationTests(unittest.TestCase):
    """chat.send response includes orchestration metadata."""

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
        goal_id = create_goal(
            self.conn, "Persona goal", "test", project_id=self.project_id
        )
        self.conn.execute(
            "update goals set status = 'active' where id = ?", (goal_id,)
        )
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

    def test_json_output_has_orchestration_key(self) -> None:
        """--mode json includes 'orchestration' object in result."""
        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", "run tests", cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("orchestration", data)
        orch = data["orchestration"]
        self.assertIn("admitted", orch)
        self.assertIn("rejected", orch)

    def test_orchestration_includes_next_action(self) -> None:
        """orchestration.next_action describes what happens next."""
        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", "fix the bug", cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        orch = data.get("orchestration", {})
        self.assertIn("next_action", orch)
        self.assertIsInstance(orch["next_action"], str)
        self.assertTrue(len(orch["next_action"]) > 0)

    def test_orchestration_includes_rejection_reasons(self) -> None:
        """When tasks are rejected, orchestration lists reasons."""
        result = _run_cli_with_home(
            self.home, "--no-tools", "--mode", "json", "-p", "fix bug",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        orch = data.get("orchestration", {})
        # With --no-tools, all proposals should be rejected.
        self.assertEqual(orch.get("admitted", -1), 0)
        self.assertIn("rejection_reasons", orch)

    def test_rpc_output_has_orchestration(self) -> None:
        """--mode rpc ResponseEnvelope result includes orchestration."""
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "run tests", cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"])
        self.assertIn("orchestration", envelope["result"])

    def test_greeting_has_empty_orchestration(self) -> None:
        """Greetings produce orchestration with zero admitted."""
        result = _run_cli_with_home(
            self.home, "--mode", "json", "-p", "你好", cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        orch = data.get("orchestration", {})
        self.assertEqual(orch.get("admitted", -1), 0)


class ChatPersonaTextModeTests(unittest.TestCase):
    """Text mode gets a one-line orchestration summary."""

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
        goal_id = create_goal(
            self.conn, "Text goal", "test", project_id=self.project_id
        )
        self.conn.execute(
            "update goals set status = 'active' where id = ?", (goal_id,)
        )
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

    def test_print_output_mentions_admitted_count(self) -> None:
        """--print output includes a short orchestration summary line."""
        result = _run_cli_with_home(
            self.home, "--print", "-p", "run tests", cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        # The summary line should mention admitted/rejected counts.
        output = result.stdout.lower()
        self.assertTrue(
            "admitted" in output or "queued" in output or "task" in output,
            "no orchestration summary in: %s" % result.stdout[:200],
        )
