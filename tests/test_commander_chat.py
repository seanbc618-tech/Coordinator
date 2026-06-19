"""Tests for the chat REPL."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.cli import _cmd_chat
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import (
    create_goal,
    get_goal,
    start_commander_run,
    finish_commander_run,
    transition_goal,
)
import argparse


def _make_args(root: Path) -> argparse.Namespace:
    return argparse.Namespace(root=str(root), db="coordinator.db")


class ChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        config_dir = self.root / "config"
        config_dir.mkdir()
        (config_dir / "agents.toml").write_text('''
[agents.echo]
command = "echo done"
capabilities = ["code"]
max_concurrency = 1
role = "worker"
''')
        (config_dir / "repos.toml").write_text('''
[repos.demo]
path = "/tmp/demo"
default_branch = "main"
remote = "origin"
branch_prefix = "coord/"
allow_push = false
merge_policy = "no_push"
verify_commands = []
''')
        (config_dir / "policy.toml").write_text('''
[task_policy]
require_single_repo = true
require_acceptance_criteria = true
require_verification_commands = false
require_handoff_summary = false
max_files_touched = 3
max_expected_minutes = 30
max_attempts = 3
split_if_touches_multiple_subsystems = false
split_if_research_and_code_are_mixed = false

[daemon_policy]
loop_interval_seconds = 300
idle_sleep_seconds = 60
run_discovery_before_tasks = true
''')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_chat_start_without_goal_is_refused(self) -> None:
        result = _cmd_chat(_make_args(self.root))
        self.assertEqual(result, 1)

    def test_chat_start_with_active_goal_is_refused(self) -> None:
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        goal_id = create_goal(conn, "Roadmap", "Finish roadmap")
        run_id = start_commander_run(conn, goal_id, "initial_plan", 1, Path("/tmp/p.md"))
        finish_commander_run(conn, run_id, status="succeeded")
        transition_goal(conn, goal_id, "active")
        conn.close()

        with patch("builtins.print") as mock_print:
            result = _cmd_chat(_make_args(self.root))
        self.assertEqual(result, 0)
        # Should print that goal is active
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("active", printed.lower())

    def test_chat_quit(self) -> None:
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        create_goal(conn, "Roadmap", "Finish roadmap")
        conn.close()

        with patch("builtins.input", side_effect=["/quit"]):
            result = _cmd_chat(_make_args(self.root))
        self.assertEqual(result, 0)

    def test_chat_start_confirms_goal(self) -> None:
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        goal_id = create_goal(conn, "Roadmap", "Finish roadmap")
        run_id = start_commander_run(conn, goal_id, "initial_plan", 1, Path("/tmp/p.md"))
        finish_commander_run(conn, run_id, status="succeeded")
        conn.close()

        with patch("builtins.input", side_effect=["/start"]):
            result = _cmd_chat(_make_args(self.root))
        self.assertEqual(result, 0)

        # Verify goal is now active
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        goal = get_goal(conn, goal_id)
        conn.close()
        self.assertEqual(goal["status"], "active")

    def test_chat_status(self) -> None:
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        create_goal(conn, "Roadmap", "Finish roadmap")
        conn.close()

        with patch("builtins.input", side_effect=["/status", "/quit"]):
            with patch("builtins.print") as mock_print:
                result = _cmd_chat(_make_args(self.root))
        self.assertEqual(result, 0)
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("draft", printed.lower())


if __name__ == "__main__":
    unittest.main()
