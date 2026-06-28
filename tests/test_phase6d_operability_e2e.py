"""Red tests for Phase 6D advanced slash commands and operability E2E.

Owner: Grok (Phase 6D Task 0)
Expected before implementation: unsupported Supervisor RPC methods.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.goals import create_goal
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
        autonomy_enabled = true
        verify_commands = ["true"]
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


class Phase6DSlashRedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.repo = self.home / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.paths.config_dir, self.repo)

        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()
        self.project_id = self.conn.execute(
            "select id from projects limit 1"
        ).fetchone()["id"]

        goal_id = create_goal(
            self.conn, "Operability goal", "test", project_id=self.project_id
        )
        self.conn.execute("update goals set status = 'active' where id = ?", (goal_id,))
        self.task_id = create_task(
            self.conn,
            title="Failed task",
            repo="test-repo",
            source_path="failed.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        self.conn.execute(
            "update tasks set state = 'failed' where id = ?",
            (self.task_id,),
        )
        self.conn.commit()

        self.server = FakeSupervisor(str(self.paths.socket), project_id=self.project_id)
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.conn.close()
        self.tmp.cleanup()

    def test_plan_slash_uses_supervisor_rpc(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/plan", cwd=self.repo)
        methods = [method for method, _ in self.server.drain_requests()]
        self.assertIn("project.plan", methods)

    def test_scan_slash_reports_read_only_diagnostics(self) -> None:
        result = _run_cli_with_home(
            self.home, "--print", "-p", "/scan", cwd=self.repo
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        methods = [method for method, _ in self.server.drain_requests()]
        self.assertIn("project.scan", methods)
        output = result.stdout.lower()
        self.assertIn("verify", output)
        self.assertIn("failed", output)

    def test_jump_slash_resolves_task_log_without_opening_editor(self) -> None:
        result = _run_cli_with_home(
            self.home,
            "--print",
            "-p",
            f"/jump {self.task_id} log",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        methods = [method for method, _ in self.server.drain_requests()]
        self.assertIn("project.jump", methods)
        self.assertNotIn("open ", result.stdout.lower())
        self.assertNotIn("cursor ", result.stdout.lower())
        self.assertNotIn("code ", result.stdout.lower())
        self.assertTrue(result.stdout.strip())