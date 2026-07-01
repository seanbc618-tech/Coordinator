"""Phase 14 red tests: morning handoff summaries from durable state.

Owner: Grok (Phase 14 Task 0)
Expected before implementation: morning_handoff module and operator.morning RPC missing.
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
from local_cli_coordinator.supervisor_events import EventBroker
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.supervisor_protocol import PROTOCOL_VERSION, RequestEnvelope
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


def _request(method: str, project_id: str, **params) -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id="req-phase14-morning",
        project_id=project_id,
        method=method,
        params=params,
    )


class MorningHandoffTests(unittest.TestCase):
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
        done_id = create_task(
            self.conn,
            title="Completed overnight",
            repo="test-repo",
            source_path="done.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        transition_task(self.conn, done_id, "done", "finished")
        failed_id = create_task(
            self.conn,
            title="Failed overnight",
            repo="test-repo",
            source_path="fail.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["false"],
            project_id=self.project_id,
        )
        transition_task(self.conn, failed_id, "failed", "verification failed")
        upsert_operator_item(
            self.conn,
            project_id=self.project_id,
            source_type="task",
            source_id=failed_id,
            severity="warning",
            title="Approve fix",
            dedupe_key=f"approval:{failed_id}",
            action_label="Approve",
            action_method="project.task.approve",
            action_params={"task_id": failed_id},
            commit=True,
        )
        self.conn.commit()
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(
            config=self.config,
            broker=EventBroker(),
            paths=self.paths,
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_morning_handoff_includes_required_sections(self) -> None:
        from local_cli_coordinator.morning_handoff import build_morning_handoff

        payload = build_morning_handoff(
            self.conn,
            paths=self.paths,
            config=self.config,
            project_id=self.project_id,
        )
        for key in (
            "completed_tasks",
            "failed_tasks",
            "pending_approvals",
            "pr_ci_changes",
            "paused_or_blocked_projects",
            "repair_recommendations",
            "agent_health_changes",
            "next_actions",
        ):
            self.assertIn(key, payload)

    def test_build_morning_handoff_persists_row(self) -> None:
        from local_cli_coordinator.morning_handoff import build_morning_handoff

        payload = build_morning_handoff(
            self.conn,
            paths=self.paths,
            config=self.config,
            scope="project",
            project_id=self.project_id,
        )
        handoff_id = payload.get("handoff_id")
        self.assertIsInstance(handoff_id, str)
        row = self.conn.execute(
            "select id, scope, project_id from morning_handoffs where id = ?",
            (handoff_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["scope"], "project")
        self.assertEqual(row["project_id"], self.project_id)

    def test_failed_tasks_reference_why(self) -> None:
        from local_cli_coordinator.morning_handoff import build_morning_handoff

        payload = build_morning_handoff(
            self.conn,
            paths=self.paths,
            config=self.config,
            project_id=self.project_id,
        )
        failed_tasks = payload.get("failed_tasks") or []
        self.assertTrue(failed_tasks)
        self.assertIn("why_ref", failed_tasks[0])

    def test_operator_morning_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("operator.morning", self.project_id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("completed_tasks", resp.result)
        self.assertIn("next_actions", resp.result)


class MorningHandoffCliTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_summary_morning_json_uses_handoff_payload(self) -> None:
        proc = _run_cli_with_home(
            self.home,
            "operator",
            "summary",
            "--morning",
            "--json",
            cwd=self.repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload.get("ok"), payload)
        data = payload.get("data") or {}
        self.assertIn("completed_tasks", data)
        self.assertIn("failed_tasks", data)
        self.assertIn("next_actions", data)


if __name__ == "__main__":
    unittest.main()