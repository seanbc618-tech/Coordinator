"""Red tests for Phase 13 approval callbacks and routing.

Owner: Grok (Phase 13 Task 0)
Expected before implementation: missing approval_callbacks module.
"""

from __future__ import annotations

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
from tests.helpers import init_git_repo


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


class ApprovalCallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
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
        self.task_id = create_task(
            self.conn,
            title="Approval task",
            repo="test-repo",
            source_path="approval.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        transition_task(self.conn, self.task_id, "awaiting_human", "needs review")
        self.conn.commit()
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(config=self.config, broker=EventBroker())

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_approval_from_destructive_operator_item(self) -> None:
        from local_cli_coordinator.approval_callbacks import (
            create_approval_from_operator_item,
        )

        item = upsert_operator_item(
            self.conn,
            project_id=self.project_id,
            source_type="task",
            source_id=self.task_id,
            severity="warning",
            title="Cancel task?",
            dedupe_key=f"cancel:{self.task_id}",
            action_label="Cancel",
            action_method="project.task.cancel",
            action_params={"task_id": self.task_id},
            commit=True,
        )
        raw, request = create_approval_from_operator_item(
            self.conn,
            project_id=self.project_id,
            operator_item_id=item.id,
            commit=True,
        )
        self.assertTrue(raw)
        self.assertEqual(request.action_method, "project.task.cancel")
        self.assertEqual(request.status, "pending")

    def test_approve_routes_through_supervisor_rpc(self) -> None:
        from local_cli_coordinator.approval_callbacks import (
            approve_approval_token,
            create_approval_from_operator_item,
        )

        item = upsert_operator_item(
            self.conn,
            project_id=self.project_id,
            source_type="task",
            source_id=self.task_id,
            severity="info",
            title="Approve task",
            dedupe_key=f"approve:{self.task_id}",
            action_label="Approve",
            action_method="project.task.approve",
            action_params={"task_id": self.task_id},
            commit=True,
        )
        raw, _request = create_approval_from_operator_item(
            self.conn,
            project_id=self.project_id,
            operator_item_id=item.id,
            commit=True,
        )
        result = approve_approval_token(
            self.conn,
            raw_token=raw,
            project_id=self.project_id,
            methods=self.methods,
            decided_by="cli",
            commit=True,
        )
        self.assertTrue(result["routed"])
        self.assertEqual(result["status"], "consumed")
        row = self.conn.execute(
            "select state from tasks where id = ?", (self.task_id,)
        ).fetchone()
        self.assertNotEqual(row["state"], "awaiting_human")

    def test_reject_does_not_mutate_task(self) -> None:
        from local_cli_coordinator.approval_callbacks import (
            create_approval_from_operator_item,
            reject_approval_token,
        )

        item = upsert_operator_item(
            self.conn,
            project_id=self.project_id,
            source_type="task",
            source_id=self.task_id,
            severity="warning",
            title="Cancel task?",
            dedupe_key=f"reject-cancel:{self.task_id}",
            action_label="Cancel",
            action_method="project.task.cancel",
            action_params={"task_id": self.task_id},
            commit=True,
        )
        raw, _request = create_approval_from_operator_item(
            self.conn,
            project_id=self.project_id,
            operator_item_id=item.id,
            commit=True,
        )
        reject_approval_token(
            self.conn,
            raw_token=raw,
            project_id=self.project_id,
            decided_by="cli",
            commit=True,
        )
        row = self.conn.execute(
            "select state from tasks where id = ?", (self.task_id,)
        ).fetchone()
        self.assertEqual(row["state"], "awaiting_human")

    def test_merge_action_rejected_without_policy(self) -> None:
        from local_cli_coordinator.approval_callbacks import route_approval_action
        from local_cli_coordinator.approval_tokens import create_approval_token

        _raw, request = create_approval_token(
            self.conn,
            project_id=self.project_id,
            action_method="project.merge",
            action_params={"task_id": self.task_id},
            commit=True,
        )
        with self.assertRaises(ValueError):
            route_approval_action(
                self.conn,
                request=request,
                methods=self.methods,
            )

    def _request(self, method: str, **params) -> RequestEnvelope:
        return RequestEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id="req-phase13",
            project_id=self.project_id,
            method=method,
            params=params,
        )


if __name__ == "__main__":
    unittest.main()