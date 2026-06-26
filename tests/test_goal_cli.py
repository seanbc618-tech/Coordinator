"""Tests for the goal CLI commands."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, get_goal, active_goal, transition_goal
from tests.helpers import run_cli


class GoalCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Create config files
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

    def test_goal_status_shows_no_goal(self) -> None:
        result = run_cli("--root", str(self.root), "goal", "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no active goal", result.stdout)

    def test_goal_without_args_shows_usage(self) -> None:
        result = run_cli("--root", str(self.root), "goal")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("usage", result.stdout.lower())

    def test_goal_text_creates_draft_preview(self) -> None:
        """Creating a goal with text should produce a draft (even if Commander fails)."""
        # This will fail because there's no commander agent configured
        result = run_cli("--root", str(self.root), "goal", "Finish", "the", "roadmap")
        # The goal should still be created as draft even though Commander fails
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        goal = active_goal(conn)
        conn.close()
        self.assertIsNotNone(goal)
        self.assertEqual(goal["status"], "draft")
        self.assertIn("preview failed", result.stdout.lower())
        self.assertNotIn("goal confirm", result.stdout.lower())


class GoalConfirmTests(unittest.TestCase):
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
        # Create a draft goal directly
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        self.goal_id = create_goal(conn, "Roadmap", "Finish roadmap")
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_goal_confirm_without_preview_says_no_preview(self) -> None:
        result = run_cli("--root", str(self.root), "goal", "confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no preview", result.stdout.lower())

    def test_goal_confirm_activates_with_preview(self) -> None:
        """If a preview run exists, confirm should activate."""
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        # Create a fake commander run
        from local_cli_coordinator.goals import start_commander_run, finish_commander_run
        run_id = start_commander_run(conn, self.goal_id, "initial_plan", 1, Path("/tmp/p.md"))
        finish_commander_run(conn, run_id, status="succeeded")
        conn.close()

        result = run_cli("--root", str(self.root), "goal", "confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("activated", result.stdout.lower())

        # Verify goal is now active
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        goal = get_goal(conn, self.goal_id)
        conn.close()
        self.assertEqual(goal["status"], "active")

    def test_goal_confirm_rejects_failed_preview(self) -> None:
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        from local_cli_coordinator.goals import start_commander_run, finish_commander_run
        run_id = start_commander_run(conn, self.goal_id, "initial_plan", 1, Path("/tmp/p.md"))
        finish_commander_run(conn, run_id, status="failed", exit_code=2, error="unsupported option")
        conn.close()

        result = run_cli("--root", str(self.root), "goal", "confirm")
        self.assertIn("preview failed", result.stdout.lower())

        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        goal = get_goal(conn, self.goal_id)
        conn.close()
        self.assertEqual(goal["status"], "draft")


class GoalPauseResumeAbandonTests(unittest.TestCase):
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
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        self.goal_id = create_goal(conn, "Roadmap", "Finish roadmap")
        # Activate it
        from local_cli_coordinator.goals import start_commander_run, finish_commander_run
        run_id = start_commander_run(conn, self.goal_id, "initial_plan", 1, Path("/tmp/p.md"))
        finish_commander_run(conn, run_id, status="succeeded")
        transition_goal(conn, self.goal_id, "active")
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_goal_pause(self) -> None:
        result = run_cli("--root", str(self.root), "goal", "pause")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("paused", result.stdout.lower())

        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        goal = get_goal(conn, self.goal_id)
        conn.close()
        self.assertEqual(goal["status"], "paused")

    def test_goal_resume(self) -> None:
        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        transition_goal(conn, self.goal_id, "paused")
        conn.close()

        result = run_cli("--root", str(self.root), "goal", "resume")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("resumed", result.stdout.lower())

    def test_goal_abandon(self) -> None:
        result = run_cli("--root", str(self.root), "goal", "abandon")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("abandoned", result.stdout.lower())

        conn = connect(self.root / "coordinator.db")
        init_db(conn)
        goal = get_goal(conn, self.goal_id)
        conn.close()
        self.assertEqual(goal["status"], "abandoned")


if __name__ == "__main__":
    unittest.main()
