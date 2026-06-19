import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.commander_runner import CommanderRunResult
from local_cli_coordinator.commander_service import maybe_replenish_goal, resume_goal
from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, get_goal, transition_goal

_PYTHON = sys.executable


def _high_risk_tasks() -> list[dict]:
    return [{
        "title": "Rotate live trading credentials",
        "repo": "demo",
        "capabilities": ["code"],
        "goal": "Store API secret for market order execution",
        "acceptance_criteria": ["Funds transfer script runs"],
        "verification_commands": [],
        "expected_files": 1,
        "expected_minutes": 10,
        "parent_task_id": None,
        "rationale": "Needs credential access",
    }]


def _write_fixture_script(tmp_dir: Path, tasks: list[dict]) -> Path:
    script = tmp_dir / "fixture_commander.py"
    script.write_text(f"""
import json
response = {{
    "schema_version": 1,
    "goal_status": "active",
    "progress_summary": "Ready",
    "tasks": {repr(tasks)},
    "stop_reason": None
}}
print(json.dumps(response))
""")
    return script


def _config(root: Path, *, command: str) -> CoordinatorConfig:
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


class CommanderFailureTests(unittest.TestCase):
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
        self.config = _config(
            self.root,
            command=f"{_PYTHON} -c 'import sys; sys.exit(1)'",
        )

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    @patch("local_cli_coordinator.commander_service.run_commander")
    def test_timeout_schedules_retry_without_losing_goal(self, mock_run) -> None:
        mock_run.return_value = CommanderRunResult(
            succeeded=False,
            response=None,
            run_id=1,
            prompt_path=self.root / "prompt.md",
            raw_output_path=self.root / "raw.txt",
            parsed_output_path=None,
            exit_code=-1,
            timed_out=True,
            error="timeout",
        )

        result = maybe_replenish_goal(self.conn, self.config, self.root)
        goal = get_goal(self.conn, self.goal_id)

        self.assertEqual(result.status, "retry_scheduled")
        self.assertEqual(goal["status"], "active")
        self.assertEqual(goal["commander_failures"], 1)
        self.assertTrue(goal["commander_retry_after"])
        self.assertEqual(mock_run.call_args.args[5], 120)

    def test_third_failure_pauses(self) -> None:
        for _ in range(3):
            maybe_replenish_goal(self.conn, self.config, self.root)

        goal = get_goal(self.conn, self.goal_id)
        self.assertEqual(goal["status"], "paused")
        self.assertEqual(goal["commander_failures"], 3)

    def test_high_risk_only_batch_blocks(self) -> None:
        script = _write_fixture_script(self.root, _high_risk_tasks())
        config = _config(self.root, command=f"{_PYTHON} {script}")

        result = maybe_replenish_goal(self.conn, config, self.root)

        self.assertEqual(result.status, "blocked_high_risk")
        goal = get_goal(self.conn, self.goal_id)
        self.assertEqual(goal["status"], "blocked")
        self.assertIn("high-risk", goal["stop_reason"].lower())

    def test_resume_clears_failure_counters(self) -> None:
        for _ in range(3):
            maybe_replenish_goal(self.conn, self.config, self.root)

        message = resume_goal(self.conn, self.goal_id)
        goal = get_goal(self.conn, self.goal_id)

        self.assertEqual(goal["status"], "active")
        self.assertEqual(goal["commander_failures"], 0)
        self.assertEqual(goal["commander_retry_after"], "")
        self.assertIn("resumed", message.lower())
