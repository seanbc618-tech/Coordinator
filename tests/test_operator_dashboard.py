"""Phase 14 red tests: daily operator dashboard payloads.

Owner: Grok (Phase 14 Task 0)
Expected before implementation: operator_dashboard module and enriched fields missing.
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

from local_cli_coordinator.config_runtime import load_config_for_paths
from local_cli_coordinator.db import connect, create_task, init_db, transition_task
from local_cli_coordinator.operator_inbox import upsert_operator_item
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import ROOT, SRC, init_git_repo

_PYTHON = sys.executable


def _write_config(config_dir: Path, repo_path: Path) -> None:
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
        review_policy = "tests_only"
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

        [notifications]
        allow_command_sink = false
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


class OperatorDashboardPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        register_project(self.conn, inspect_project(self.repo), confirmed=True)
        self.conn.commit()
        self.project_id = self.conn.execute(
            "select id from projects limit 1"
        ).fetchone()["id"]
        self.config = load_config_for_paths(self.paths)
        self.task_id = create_task(
            self.conn,
            title="Secret dashboard task title",
            repo="test-repo",
            source_path="dash.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["false"],
            project_id=self.project_id,
        )
        transition_task(self.conn, self.task_id, "failed", "verification failed")
        upsert_operator_item(
            self.conn,
            project_id=self.project_id,
            source_type="task",
            source_id=self.task_id,
            severity="warning",
            title="Needs approval",
            dedupe_key=f"approval:{self.task_id}",
            action_label="Approve",
            action_method="project.task.approve",
            action_params={"task_id": self.task_id},
            commit=True,
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_daily_dashboard_includes_operator_fields(self) -> None:
        from local_cli_coordinator.operator_dashboard import build_daily_dashboard

        payload = build_daily_dashboard(self.conn, paths=self.paths, config=self.config)
        self.assertIn("global_pause", payload)
        self.assertIn("project_count", payload)
        self.assertIn("active_project_count", payload)
        self.assertIn("tasks_by_state", payload)
        self.assertIn("running_workers", payload)
        self.assertIn("failed_count", payload)
        self.assertIn("blocked_count", payload)
        self.assertIn("awaiting_approval_count", payload)
        self.assertIn("pending_approval_count", payload)
        self.assertIn("pr_ci_attention_count", payload)
        self.assertIn("unhealthy_agent_count", payload)
        self.assertIn("morning_handoff_at", payload)
        self.assertIn("next_actions", payload)

    def test_global_dashboard_redacts_cross_project_task_titles(self) -> None:
        from local_cli_coordinator.operator_dashboard import build_daily_dashboard

        payload = build_daily_dashboard(self.conn, paths=self.paths, config=self.config)
        serialized = json.dumps(payload)
        self.assertNotIn("Secret dashboard task title", serialized)

    def test_project_dashboard_includes_failure_summaries(self) -> None:
        from local_cli_coordinator.operator_dashboard import build_project_dashboard

        payload = build_project_dashboard(
            self.conn,
            project_id=self.project_id,
            paths=self.paths,
            config=self.config,
        )
        self.assertEqual(payload.get("project_id"), self.project_id)
        self.assertIn("failure_summaries", payload)
        summaries = payload.get("failure_summaries") or []
        self.assertTrue(summaries)


class OperatorDashboardRpcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        register_project(self.conn, inspect_project(self.repo), confirmed=True)
        self.conn.commit()
        self._orig_home = os.environ.get("COORDINATOR_HOME")
        os.environ["COORDINATOR_HOME"] = str(self.home)

    def tearDown(self) -> None:
        self.conn.close()
        if self._orig_home is not None:
            os.environ["COORDINATOR_HOME"] = self._orig_home
        else:
            os.environ.pop("COORDINATOR_HOME", None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dashboard_rpc_includes_phase14_fields(self) -> None:
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/dashboard", cwd=self.repo
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout.strip().splitlines()[0])
        self.assertTrue(envelope["ok"], envelope)
        payload = envelope["result"]
        self.assertIn("global_pause", payload)
        self.assertIn("next_actions", payload)


if __name__ == "__main__":
    unittest.main()