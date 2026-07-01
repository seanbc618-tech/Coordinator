"""Phase 14 red tests: deterministic task failure explanations.

Owner: Grok (Phase 14 Task 0)
Expected before implementation: failure_explainer module and operator.explain_failure RPC missing.
"""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config_runtime import load_config_for_paths
from local_cli_coordinator.db import (
    connect,
    create_task,
    finish_attempt,
    init_db,
    start_attempt,
    transition_task,
)
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_events import EventBroker
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.supervisor_protocol import PROTOCOL_VERSION, RequestEnvelope

_PYTHON = None


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


def _request(method: str, project_id: str, **params) -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id="req-phase14-failure",
        project_id=project_id,
        method=method,
        params=params,
    )


class FailureExplainerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        from tests.helpers import init_git_repo

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
            title="Explain me",
            repo="test-repo",
            source_path="fail.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["false"],
            project_id=self.project_id,
        )
        transition_task(self.conn, self.task_id, "failed", "verification failed")
        attempt_id = start_attempt(
            self.conn,
            self.task_id,
            agent_id="worker",
            command="true",
        )
        finish_attempt(
            self.conn,
            attempt_id,
            exit_code=1,
            result_class="verification_failed",
            result_reason="command false exited 1",
            log_path=str(self.paths.state_dir / "attempt.log"),
        )
        log_path = Path(self.paths.state_dir / "attempt.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            "api_key=super-secret-token\n" + "\n".join(f"line {i}" for i in range(30)),
            encoding="utf-8",
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

    def test_explain_task_failure_returns_classified_reason(self) -> None:
        from local_cli_coordinator.failure_explainer import explain_task_failure

        payload = explain_task_failure(self.conn, task_id=self.task_id)
        self.assertEqual(payload.get("task_id"), self.task_id)
        self.assertEqual(payload.get("status"), "failed")
        self.assertEqual(payload.get("assigned_agent"), "worker")
        self.assertIn("latest_attempt", payload)
        self.assertEqual(
            payload.get("classified_reason"),
            "verification_failed",
        )
        self.assertIn("next_action", payload)

    def test_explain_task_failure_redacts_secrets(self) -> None:
        from local_cli_coordinator.failure_explainer import explain_task_failure

        payload = explain_task_failure(self.conn, task_id=self.task_id)
        serialized = json.dumps(payload)
        self.assertNotIn("super-secret-token", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_explain_task_failure_limits_log_lines(self) -> None:
        from local_cli_coordinator.failure_explainer import explain_task_failure

        payload = explain_task_failure(self.conn, task_id=self.task_id)
        log_lines = payload.get("log_lines") or []
        self.assertLessEqual(len(log_lines), 20)

    def test_operator_explain_failure_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("operator.explain_failure", self.project_id, task_id=self.task_id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result.get("task_id"), self.task_id)
        self.assertIn("classified_reason", resp.result)


if __name__ == "__main__":
    unittest.main()