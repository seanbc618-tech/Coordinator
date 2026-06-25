"""Tests for daemon queue replenishment."""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.commander_service import ReplenishmentResult, maybe_replenish_goal
from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, create_task, init_db, transition_task
from local_cli_coordinator.goals import (
    active_goal,
    create_goal,
    get_goal,
    link_task_to_goal,
    transition_goal,
)

_PYTHON = sys.executable


def _write_fixture_script(tmp_dir: Path, tasks: list[dict] | None = None) -> Path:
    """Write a Python script that outputs a Commander response."""
    if tasks is None:
        tasks = [{
            "title": "Add feature file",
            "repo": "demo",
            "capabilities": ["code"],
            "goal": "Create feature.txt",
            "acceptance_criteria": ["feature.txt exists"],
            "verification_commands": [],
            "expected_files": 1,
            "expected_minutes": 10,
            "parent_task_id": None,
            "rationale": "First step toward goal",
        }]

    script = tmp_dir / "fixture_commander.py"
    intent = "task_request" if tasks else "conversation"
    user_reply = (
        "I'll queue the next slice for the goal."
        if tasks
        else "Nothing new to queue right now."
    )
    # Use repr() to get valid Python literals (None instead of null)
    script.write_text(f'''
import json
response = {{
    "schema_version": 2,
    "intent": {intent!r},
    "user_reply": {user_reply!r},
    "goal_status": "active",
    "progress_summary": "Ready",
    "tasks": {repr(tasks)},
    "stop_reason": None
}}
print(json.dumps(response))
''')
    return script


def _test_config(tmp_dir: Path, command: str | None = None) -> CoordinatorConfig:
    if command is None:
        script = _write_fixture_script(tmp_dir)
        command = f"{_PYTHON} {script}"
    repo_path = tmp_dir / "repo"
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


class ReplenishmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)
        self.config = _test_config(self.root)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _create_active_goal(self) -> int:
        goal_id = create_goal(
            self.conn, "Roadmap", "Finish roadmap",
            repo_ids=["demo"],
        )
        transition_goal(self.conn, goal_id, "active")
        return goal_id

    def test_no_goal_returns_not_eligible(self) -> None:
        result = maybe_replenish_goal(self.conn, self.config, self.root)
        self.assertEqual(result.status, "not_eligible")

    def test_draft_goal_returns_not_eligible(self) -> None:
        create_goal(self.conn, "Roadmap", "Finish roadmap")
        result = maybe_replenish_goal(self.conn, self.config, self.root)
        self.assertEqual(result.status, "not_eligible")

    def test_active_goal_with_ready_tasks_returns_queue_not_low(self) -> None:
        goal_id = self._create_active_goal()
        task_id = create_task(
            self.conn, title="T", repo="demo", source_path="",
            priority="normal", capabilities=["code"], goal="G",
            acceptance_criteria=["A"], verification_commands=[],
        )
        link_task_to_goal(self.conn, goal_id, task_id)
        result = maybe_replenish_goal(self.conn, self.config, self.root)
        self.assertEqual(result.status, "queue_not_low")

    def test_active_empty_goal_replenishes(self) -> None:
        self._create_active_goal()
        result = maybe_replenish_goal(self.conn, self.config, self.root)
        self.assertEqual(result.status, "admitted")
        self.assertGreater(len(result.admitted_task_ids), 0)

    def test_nonactive_goal_does_not_replenish(self) -> None:
        for state in ("draft", "paused", "completed"):
            goal_id = create_goal(self.conn, f"Goal {state}", "obj")
            if state != "draft":
                transition_goal(self.conn, goal_id, state)
            result = maybe_replenish_goal(self.conn, self.config, self.root)
            self.assertEqual(result.status, "not_eligible")
            # Clean up: transition to terminal state
            if state == "draft":
                transition_goal(self.conn, goal_id, "abandoned")
            elif state == "paused":
                transition_goal(self.conn, goal_id, "abandoned")


class ReplenishmentWithFixedCommanderTests(unittest.TestCase):
    """Tests that use a specific Commander response."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_commander_failed_records_failure(self) -> None:
        """If Commander fails, failure count should increase."""
        config = _test_config(self.root, command=f"{_PYTHON} -c 'import sys; sys.exit(1)'")
        goal_id = create_goal(
            self.conn, "Roadmap", "Finish roadmap", repo_ids=["demo"],
        )
        transition_goal(self.conn, goal_id, "active")

        result = maybe_replenish_goal(self.conn, config, self.root)
        self.assertEqual(result.status, "commander_failed")

        goal = get_goal(self.conn, goal_id)
        self.assertEqual(goal["commander_failures"], 1)


if __name__ == "__main__":
    unittest.main()
