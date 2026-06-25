"""End-to-end tests for the Commander feature.

Tests the full lifecycle: goal creation, confirmation, daemon replenishment,
task processing, and goal completion.
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.commander_service import (
    confirm_goal,
    create_and_preview_goal,
    maybe_replenish_goal,
)
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
    list_commander_runs,
    list_linked_tasks,
    transition_goal,
)

_PYTHON = sys.executable


def _write_two_batch_fixture(tmp_dir: Path) -> Path:
    """Write a Commander fixture that proposes tasks in two batches.

    First call: proposes "first.txt"
    Second call: sees "first.txt" done, proposes "second.txt"
    Third call: all done, completes
    """
    script = tmp_dir / "fixture_commander.py"
    script.write_text('''
import json
from pathlib import Path

state_file = Path(__file__).parent / "commander_state.json"
if state_file.exists():
    state = json.loads(state_file.read_text())
else:
    state = {"call_count": 0}

state["call_count"] = state.get("call_count", 0) + 1
state_file.write_text(json.dumps(state))

if state["call_count"] == 1:
    response = {
        "schema_version": 2,
        "intent": "task_request",
        "user_reply": "Starting with the first slice: create first.txt.",
        "goal_status": "active",
        "progress_summary": "First batch ready",
        "tasks": [{
            "title": "Create first.txt",
            "repo": "demo",
            "capabilities": ["code"],
            "goal": "Create first.txt with content",
            "acceptance_criteria": ["first.txt exists"],
            "verification_commands": [],
            "expected_files": 1,
            "expected_minutes": 5,
            "parent_task_id": None,
            "rationale": "First step toward goal"
        }],
        "stop_reason": None
    }
elif state["call_count"] == 2:
    response = {
        "schema_version": 2,
        "intent": "task_request",
        "user_reply": "First slice is done; next I'll create second.txt.",
        "goal_status": "active",
        "progress_summary": "Second batch ready",
        "tasks": [{
            "title": "Create second.txt",
            "repo": "demo",
            "capabilities": ["code"],
            "goal": "Create second.txt with content",
            "acceptance_criteria": ["second.txt exists"],
            "verification_commands": [],
            "expected_files": 1,
            "expected_minutes": 5,
            "parent_task_id": None,
            "rationale": "Second step, depends on first"
        }],
        "stop_reason": None
    }
else:
    response = {
        "schema_version": 2,
        "intent": "conversation",
        "user_reply": "All slices are complete — the goal is finished.",
        "goal_status": "completed",
        "progress_summary": "All tasks done",
        "tasks": [],
        "stop_reason": "Goal achieved"
    }

print(json.dumps(response))
''')
    return script


def _e2e_config(tmp_dir: Path) -> CoordinatorConfig:
    """Create a config for end-to-end testing."""
    script = _write_two_batch_fixture(tmp_dir)
    repo_path = tmp_dir / "repo"
    repo_path.mkdir(exist_ok=True)

    return CoordinatorConfig(
        agents={
            "codex_commander": AgentConfig(
                id="codex_commander",
                command=f"{_PYTHON} {script}",
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


class TwoBatchEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Clean up any leftover state file
        state_file = self.root / "commander_state.json"
        if state_file.exists():
            state_file.unlink()
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)
        self.config = _e2e_config(self.root)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_two_dependent_batches_complete_goal(self) -> None:
        """Test that two batches of tasks can complete a goal."""
        # Create and preview goal (this calls Commander once - call_count=1)
        preview = create_and_preview_goal(
            self.conn, self.config, self.root, "Build two slices",
        )
        self.assertGreater(len(preview.proposals), 0)

        # Confirm goal
        result = confirm_goal(
            self.conn, self.config, self.root, goal_id=preview.goal_id
        )
        self.assertIn("activated", result.lower())

        # First replenishment (call_count=2, proposes second task)
        first = maybe_replenish_goal(self.conn, self.config, self.root)
        self.assertEqual(first.status, "admitted")
        self.assertGreater(len(first.admitted_task_ids), 0)

        # Mark all ready tasks as done
        for t in list_linked_tasks(self.conn, preview.goal_id):
            if t["state"] == "ready":
                transition_task(self.conn, t["id"], "done", "test completed")

        # Second replenishment (call_count=3, completes goal)
        second = maybe_replenish_goal(self.conn, self.config, self.root)

        # Verify goal is completed
        goal = get_goal(self.conn, preview.goal_id)
        self.assertEqual(goal["status"], "completed")

        # Verify Commander runs were recorded
        runs = list_commander_runs(self.conn, preview.goal_id)
        self.assertGreaterEqual(len(runs), 2)

    def test_goal_without_commander_agent_fails_gracefully(self) -> None:
        """Test that a goal without a Commander agent doesn't crash."""
        config_no_commander = CoordinatorConfig(
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
                    path=self.root / "repo",
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

        # Create goal
        goal_id = create_goal(self.conn, "Test", "Test goal", repo_ids=["demo"])
        transition_goal(self.conn, goal_id, "active")

        # Replenishment should fail gracefully
        result = maybe_replenish_goal(self.conn, config_no_commander, self.root)
        self.assertEqual(result.status, "not_eligible")


if __name__ == "__main__":
    unittest.main()
