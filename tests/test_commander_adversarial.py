"""Adversarial acceptance tests for Commander safety boundaries."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.commander_runner import build_commander_context, run_commander
from local_cli_coordinator.commander_service import maybe_replenish_goal
from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, create_task, init_db, task_counts, transition_task
from local_cli_coordinator.engine import run_daemon_cycle
from local_cli_coordinator.goals import (
    create_goal,
    get_goal,
    get_latest_commander_run,
    link_task_to_goal,
    list_commander_runs,
    start_commander_run,
    transition_goal,
)

_PYTHON = sys.executable

TASK_MARKDOWN = """
# Task: Inbox slice

repo: demo
priority: normal
capabilities: [code]
verification: [python -m unittest]

## Goal

Ship inbox task.

## Acceptance Criteria

- Works.
"""


def _write_config(root: Path) -> None:
    (root / "config").mkdir()
    (root / "config" / "agents.toml").write_text(textwrap.dedent("""
        [agents.codex_commander]
        command = "python {prompt_path}"
        capabilities = ["code", "tests", "docs", "research"]
        max_concurrency = 1
        role = "commander"

        [agents.echo]
        command = "echo done"
        capabilities = ["code"]
        max_concurrency = 1
        role = "worker"
    """).strip())
    (root / "config" / "repos.toml").write_text(textwrap.dedent("""
        [repos.demo]
        path = "/tmp/demo"
        default_branch = "main"
        remote = "origin"
        branch_prefix = "coord/"
        allow_push = false
        merge_policy = "no_push"
        verify_commands = ["python -m unittest"]
    """).strip())
    (root / "config" / "policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = true
        require_acceptance_criteria = true
        require_verification_commands = true
        require_handoff_summary = true
        max_files_touched = 3
        max_expected_minutes = 30
        max_attempts = 3
        split_if_touches_multiple_subsystems = true
        split_if_research_and_code_are_mixed = true
    """).strip())


def _config(root: Path, command: str) -> CoordinatorConfig:
    repo_path = root / "repo"
    repo_path.mkdir(exist_ok=True)
    return CoordinatorConfig(
        agents={
            "codex_commander": AgentConfig(
                id="codex_commander",
                command=command,
                capabilities=["code", "tests", "docs", "research"],
                max_concurrency=1,
                role="commander",
            ),
            "echo": AgentConfig(
                id="echo",
                command="echo done",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        },
        repos={
            "demo": RepoConfig(
                id="demo",
                path=repo_path,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=[],
            ),
        },
        policy=PolicyConfig(
            require_single_repo=True,
            require_acceptance_criteria=True,
            require_verification_commands=False,
            require_handoff_summary=False,
            max_files_touched=3,
            max_expected_minutes=30,
            max_attempts=3,
            split_if_touches_multiple_subsystems=False,
            split_if_research_and_code_are_mixed=False,
        ),
        daemon_policy=DaemonPolicyConfig(),
    )


def _proposal_task() -> dict:
    return {
        "title": "Add helper",
        "repo": "demo",
        "capabilities": ["code"],
        "goal": "Ship helper",
        "acceptance_criteria": ["Helper exists"],
        "verification_commands": [],
        "expected_files": 1,
        "expected_minutes": 10,
        "parent_task_id": None,
        "rationale": "Advances goal",
    }


def _write_response_script(root: Path, response: dict) -> str:
    script = root / "commander.py"
    script.write_text(
        "import json\n"
        f"print(json.dumps({response!r}))\n"
    )
    return f"{_PYTHON} {script}"


class CommanderAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)
        self.goal_id = create_goal(
            self.conn,
            "Roadmap",
            "Finish roadmap",
            repo_ids=["demo"],
        )
        transition_goal(self.conn, self.goal_id, "active")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_worker_role_cannot_become_commander(self) -> None:
        config = CoordinatorConfig(
            agents={
                "echo": AgentConfig(
                    id="echo",
                    command="echo done",
                    capabilities=["code"],
                    max_concurrency=1,
                    role="worker",
                ),
            },
            repos={},
            policy=PolicyConfig(
                require_single_repo=True,
                require_acceptance_criteria=True,
                require_verification_commands=False,
                require_handoff_summary=False,
                max_files_touched=3,
                max_expected_minutes=30,
                max_attempts=3,
                split_if_touches_multiple_subsystems=False,
                split_if_research_and_code_are_mixed=False,
            ),
            daemon_policy=DaemonPolicyConfig(),
        )
        with self.assertRaisesRegex(ValueError, "commander"):
            run_commander(self.conn, config, self.root, self.goal_id, "replenishment", 30)

    def test_context_does_not_copy_environment_values(self) -> None:
        os.environ["COORDINATOR_SECRET_TOKEN"] = "super-secret-value"
        self.addCleanup(os.environ.pop, "COORDINATOR_SECRET_TOKEN", None)

        context = build_commander_context(
            self.conn,
            _config(self.root, f"{_PYTHON} -c 'print(1)'"),
            self.root,
            self.goal_id,
        )

        self.assertNotIn("COORDINATOR_SECRET_TOKEN", context)
        self.assertNotIn("super-secret-value", context)

    def test_completion_cannot_skip_nonterminal_tasks(self) -> None:
        task_id = create_task(
            self.conn,
            title="Still running",
            repo="demo",
            source_path="tasks/generated/running.md",
            priority="normal",
            capabilities=["code"],
            goal="Finish slice",
            acceptance_criteria=["Done"],
            verification_commands=[],
        )
        link_task_to_goal(self.conn, self.goal_id, task_id)
        transition_task(self.conn, task_id, "running", "worker started")

        response = {
            "schema_version": 2,
            "intent": "conversation",
            "user_reply": "All linked tasks are finished.",
            "goal_status": "completed",
            "progress_summary": "All done",
            "tasks": [],
            "stop_reason": "Criteria satisfied",
        }
        config = _config(self.root, _write_response_script(self.root, response))

        result = maybe_replenish_goal(self.conn, config, self.root)

        goal = get_goal(self.conn, self.goal_id)
        self.assertEqual(goal["status"], "active")
        self.assertNotEqual(result.status, "completed")

    def test_restart_interrupts_stale_runs(self) -> None:
        stale_id = start_commander_run(
            self.conn,
            self.goal_id,
            "replenishment",
            1,
            Path("runs/commander/stale/prompt.md"),
        )
        config = _config(
            self.root,
            _write_response_script(
                self.root,
                {
                    "schema_version": 2,
                    "intent": "task_request",
                    "user_reply": "Proposing a helper slice to advance the goal.",
                    "goal_status": "active",
                    "progress_summary": "Ready",
                    "tasks": [_proposal_task()],
                    "stop_reason": None,
                },
            ),
        )

        run_commander(self.conn, config, self.root, self.goal_id, "replenishment", 30)

        runs = list_commander_runs(self.conn, self.goal_id)
        stale = next(row for row in runs if row["id"] == stale_id)
        latest = get_latest_commander_run(self.conn, self.goal_id)

        self.assertEqual(stale["status"], "interrupted")
        self.assertEqual(latest["status"], "succeeded")

    def test_replenishment_admits_one_fingerprint(self) -> None:
        config = _config(
            self.root,
            _write_response_script(
                self.root,
                {
                    "schema_version": 2,
                    "intent": "task_request",
                    "user_reply": "Proposing a helper slice to advance the goal.",
                    "goal_status": "active",
                    "progress_summary": "Ready",
                    "tasks": [_proposal_task()],
                    "stop_reason": None,
                },
            ),
        )

        first = maybe_replenish_goal(self.conn, config, self.root)
        self.assertEqual(len(first.admitted_task_ids), 1)
        transition_task(
            self.conn,
            first.admitted_task_ids[0],
            "done",
            "completed for duplicate test",
        )

        second = maybe_replenish_goal(self.conn, config, self.root)

        self.assertEqual(len(second.admitted_task_ids), 0)
        self.assertTrue(
            any("duplicate fingerprint" in reason for reason in second.rejected_reasons)
        )

        links = self.conn.execute(
            "select proposal_fingerprint from task_goal_links where goal_id = ?",
            (self.goal_id,),
        ).fetchall()
        fingerprints = [row["proposal_fingerprint"] for row in links if row["proposal_fingerprint"]]
        self.assertEqual(len(fingerprints), 1)

    def test_daemon_without_goal_preserves_inbox_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            inbox = root / "tasks" / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "one.md").write_text(textwrap.dedent(TASK_MARKDOWN).strip())

            config = CoordinatorConfig(
                agents={
                    "echo": AgentConfig(
                        id="echo",
                        command="echo done",
                        capabilities=["code"],
                        max_concurrency=1,
                        role="worker",
                    ),
                },
                repos={
                    "demo": RepoConfig(
                        id="demo",
                        path=root / "repo",
                        default_branch="main",
                        remote="origin",
                        branch_prefix="coord/",
                        allow_push=False,
                        merge_policy="no_push",
                        verify_commands=["python -m unittest"],
                    ),
                },
                policy=PolicyConfig(
                    require_single_repo=True,
                    require_acceptance_criteria=True,
                    require_verification_commands=True,
                    require_handoff_summary=True,
                    max_files_touched=3,
                    max_expected_minutes=30,
                    max_attempts=3,
                    split_if_touches_multiple_subsystems=True,
                    split_if_research_and_code_are_mixed=True,
                ),
                daemon_policy=DaemonPolicyConfig(),
            )
            (root / "repo").mkdir()

            result = run_daemon_cycle(conn, config, root)
            counts = task_counts(conn)
            conn.close()

        self.assertEqual(result.commander_status, "not_eligible")
        self.assertEqual(result.commander_tasks_admitted, 0)
        self.assertGreaterEqual(result.imported_tasks, 1)
        self.assertGreaterEqual(sum(counts.values()), 1)