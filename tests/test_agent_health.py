"""Phase 14 red tests: agent health snapshots from durable state.

Owner: Grok (Phase 14 Task 0)
Expected before implementation: agent_health module and operator.health RPC missing.
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
)
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
        max_concurrency = 2
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
        request_id="req-phase14-health",
        project_id=project_id,
        method=method,
        params=params,
    )


class AgentHealthTests(unittest.TestCase):
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
        self.task_id = create_task(
            self.conn,
            title="Health task",
            repo="test-repo",
            source_path="health.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        for exit_code in (0, 1, 1, 1):
            attempt_id = start_attempt(
                self.conn,
                self.task_id,
                agent_id="worker",
                command="true",
            )
            finish_attempt(
                self.conn,
                attempt_id,
                exit_code=exit_code,
                result_class="success" if exit_code == 0 else "failure",
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

    def test_compute_agent_health_from_attempts(self) -> None:
        from local_cli_coordinator.agent_health import compute_agent_health

        snapshots = compute_agent_health(
            self.conn,
            config=self.config,
            project_id=self.project_id,
        )
        worker = next(item for item in snapshots if item["agent_id"] == "worker")
        metrics = worker.get("metrics") or {}
        self.assertGreaterEqual(metrics.get("attempts_total", 0), 4)
        self.assertGreaterEqual(metrics.get("failures", 0), 3)
        self.assertEqual(worker.get("max_concurrency"), 2)
        self.assertIn(worker.get("recommendation"), {
            "ok",
            "reduce_concurrency",
            "disable_temporarily",
            "check_command",
            "review_failures",
        })

    def test_snapshot_agent_health_persists_row(self) -> None:
        from local_cli_coordinator.agent_health import snapshot_agent_health

        saved = snapshot_agent_health(
            self.conn,
            config=self.config,
            project_id=self.project_id,
        )
        self.assertTrue(saved)
        row = self.conn.execute(
            "select agent_id, status, recommendation from agent_health_snapshots "
            "where agent_id = 'worker' order by created_at desc limit 1"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn(row["status"], {"healthy", "degraded", "unavailable", "disabled"})

    def test_operator_health_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("operator.health", self.project_id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("agents", resp.result)
        agents = resp.result.get("agents") or []
        self.assertTrue(agents)
        self.assertEqual(agents[0].get("agent_id"), "worker")


if __name__ == "__main__":
    unittest.main()