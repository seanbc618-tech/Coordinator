"""Red tests for Phase 6D worker-state snapshots.

Owner: Grok (Phase 6D Task 0)
Expected before implementation: missing ``worker_state`` module or table.
"""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from local_cli_coordinator.config import (
    AgentConfig,
    AutonomyConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, create_task, init_db, transition_task
from local_cli_coordinator.engine import run_worker_attempt
from local_cli_coordinator.task_control import cancel_task
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class WorkerStateRedTests(unittest.TestCase):
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
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        self.project_id = register_project(self.conn, draft, confirmed=True)
        self.conn.commit()

        self.task_id = create_task(
            self.conn,
            title="Snapshot task",
            repo="test-repo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        self.conn.commit()

        self.config = CoordinatorConfig(
            agents={
                "worker": AgentConfig(
                    id="worker",
                    command="true",
                    capabilities=["code"],
                    max_concurrency=1,
                    role="worker",
                )
            },
            repos={
                "test-repo": RepoConfig(
                    id="test-repo",
                    path=self.repo,
                    default_branch="main",
                    remote="origin",
                    branch_prefix="coord/",
                    allow_push=False,
                    merge_policy="no_push",
                    verify_commands=[],
                )
            },
            policy=PolicyConfig(
                require_single_repo=False,
                require_acceptance_criteria=False,
                require_verification_commands=False,
                require_handoff_summary=False,
                max_files_touched=20,
                max_expected_minutes=60,
                max_attempts=3,
                split_if_touches_multiple_subsystems=False,
                split_if_research_and_code_are_mixed=False,
            ),
            daemon_policy=DaemonPolicyConfig(),
            autonomy=AutonomyConfig(),
            discovery_sources=[],
            connectors=[],
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_worker_attempt_writes_post_attempt_snapshot(self) -> None:
        from local_cli_coordinator.worker_state import list_worker_state_snapshots

        agent = self.config.agents["worker"]
        prompt = self.tmp / "prompt.md"
        prompt.write_text("do work")
        worktree = self.repo
        run_dir = self.tmp / "runs" / self.task_id
        run_dir.mkdir(parents=True)

        with mock.patch(
            "local_cli_coordinator.engine.run_agent",
            return_value=mock.Mock(
                exit_code=0,
                timed_out=False,
                log_path=run_dir / "agent.log",
            ),
        ):
            (run_dir / "agent.log").write_text("ok\n")
            run_worker_attempt(
                self.conn,
                self.config,
                self.task_id,
                agent,
                prompt,
                worktree,
                run_dir,
            )
        self.conn.commit()

        snapshots = list_worker_state_snapshots(
            self.conn, project_id=self.project_id, task_id=self.task_id
        )
        self.assertTrue(snapshots)
        latest = snapshots[0]
        self.assertEqual(latest["state_type"], "post_attempt")
        payload = latest["payload"]
        self.assertEqual(payload["task_id"], self.task_id)
        self.assertEqual(payload["exit_code"], 0)
        self.assertFalse(payload["timed_out"])

    def test_worker_snapshot_redacts_environment_secrets(self) -> None:
        from local_cli_coordinator.worker_state import redact_worker_state

        raw = {
            "task_id": "task-1",
            "command": ["grok", "--token", "sk-secret-token"],
            "env": {"OPENAI_API_KEY": "sk-secret-token", "PATH": "/usr/bin"},
            "log_path": "/tmp/agent.log",
        }
        redacted = redact_worker_state(raw)
        encoded = json.dumps(redacted)
        self.assertNotIn("sk-secret-token", encoded)
        self.assertNotIn("OPENAI_API_KEY", encoded)
        self.assertIn("[REDACTED]", encoded)
        self.assertNotIn("env", encoded.lower())

    def test_worker_launch_exception_writes_post_attempt_snapshot(self) -> None:
        from local_cli_coordinator.worker_state import list_worker_state_snapshots

        agent = AgentConfig(
            id="worker",
            command="grok --token sk-secret-token",
            capabilities=["code"],
            max_concurrency=1,
            role="worker",
        )
        prompt = self.tmp / "prompt.md"
        prompt.write_text("do work")
        worktree = self.repo
        run_dir = self.tmp / "runs" / self.task_id
        run_dir.mkdir(parents=True)

        with mock.patch(
            "local_cli_coordinator.engine.run_agent",
            side_effect=RuntimeError("agent launch failed"),
        ):
            with self.assertRaises(RuntimeError):
                run_worker_attempt(
                    self.conn,
                    self.config,
                    self.task_id,
                    agent,
                    prompt,
                    worktree,
                    run_dir,
                )
        self.conn.commit()

        snapshots = list_worker_state_snapshots(
            self.conn, project_id=self.project_id, task_id=self.task_id
        )
        self.assertTrue(snapshots)
        latest = snapshots[0]
        self.assertEqual(latest["state_type"], "post_attempt")
        payload = latest["payload"]
        self.assertEqual(payload["exit_code"], 127)
        encoded = json.dumps(payload)
        self.assertNotIn("sk-secret-token", encoded)
        self.assertNotIn("OPENAI_API_KEY", encoded)

    def test_cancel_running_task_writes_cancellation_snapshot(self) -> None:
        from local_cli_coordinator.worker_state import list_worker_state_snapshots

        transition_task(self.conn, self.task_id, "running", "assigned to worker")
        self.conn.commit()

        cancel_task(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
        )
        self.conn.commit()

        snapshots = list_worker_state_snapshots(
            self.conn, project_id=self.project_id, task_id=self.task_id
        )
        self.assertTrue(snapshots)
        latest = snapshots[0]
        self.assertEqual(latest["state_type"], "cancellation")
        self.assertEqual(latest["payload"]["previous_state"], "running")