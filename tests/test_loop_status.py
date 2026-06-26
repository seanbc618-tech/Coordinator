import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, transition_goal
from tests.helpers import run_cli


def _write_config(root: Path) -> None:
    config_dir = root / "config"
    config_dir.mkdir()
    (config_dir / "agents.toml").write_text(textwrap.dedent("""
        [agents.fake]
        command = "python -c 'print(1)'"
        capabilities = ["code"]
        max_concurrency = 1
    """).strip())
    (config_dir / "repos.toml").write_text(textwrap.dedent("""
        [repos.demo]
        path = "/tmp/demo"
        default_branch = "main"
        remote = "origin"
        branch_prefix = "coord/"
        allow_push = false
        merge_policy = "no_push"
        verify_commands = ["python -m unittest"]
    """).strip())
    (config_dir / "policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = true
        require_acceptance_criteria = true
        require_verification_commands = true
        require_handoff_summary = false
        max_files_touched = 3
        max_expected_minutes = 30
        max_attempts = 3
        split_if_touches_multiple_subsystems = true
        split_if_research_and_code_are_mixed = true
    """).strip())


class LoopStatusTests(unittest.TestCase):
    def test_status_loop_reports_readiness_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Loop Status", result.stdout)
        self.assertIn("Readiness:", result.stdout)

    def test_status_loop_reports_lock_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Lock:", result.stdout)

    def test_status_loop_reports_circuit_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Circuit breaker:", result.stdout)

    def test_status_loop_reports_active_leases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Active leases: 0", result.stdout)

    def test_status_loop_reports_tasks_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Tasks:", result.stdout)

    def test_status_loop_reports_human_review_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Human review pending:", result.stdout)

    def test_status_without_loop_shows_original_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Loop Status", result.stdout)
        self.assertIn("no tasks", result.stdout)

    def test_status_loop_with_config_does_not_crash_on_closed_db(self) -> None:
        """Regression: circuit_breaker_reason() was called after conn.close()."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            result = run_cli("--root", str(root), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Circuit breaker:", result.stdout)

    def test_status_loop_reports_last_run_and_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            result = run_cli("--root", str(root), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Last run:", result.stdout)
        self.assertIn("Next run:", result.stdout)

    def test_status_loop_reports_budget_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            result = run_cli("--root", str(root), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("budget:", result.stdout)
        self.assertIn("tasks today", result.stdout)

    def test_status_loop_uses_daemon_policy_interval_with_last_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            (root / "config" / "policy.toml").write_text(textwrap.dedent("""
                [task_policy]
                require_single_repo = true
                require_acceptance_criteria = true
                require_verification_commands = true
                require_handoff_summary = false
                max_files_touched = 3
                max_expected_minutes = 30
                max_attempts = 3
                split_if_touches_multiple_subsystems = true
                split_if_research_and_code_are_mixed = true

                [daemon_policy]
                loop_interval_seconds = 600
            """).strip())
            conn = connect(root / "coordinator.db")
            init_db(conn)
            conn.execute(
                "insert into daemon_runs(started_at, ended_at, tasks_processed, failures) "
                "values ('2026-06-19T10:00:00Z', '2026-06-19T10:05:00Z', 1, 0)"
            )
            conn.commit()
            conn.close()

            result = run_cli("--root", str(root), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Last run:", result.stdout)
        self.assertIn("Next run: ~600s after last run", result.stdout)

    def test_empty_active_goal_waits_for_replenishment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            goal_id = create_goal(
                conn,
                "Roadmap",
                "Finish roadmap",
                completion_criteria=[],
                constraints=[],
                repo_ids=["demo"],
            )
            transition_goal(conn, goal_id, "active")
            conn.close()

            result = run_cli("--root", str(root), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Goal: active", result.stdout)
        self.assertIn("waiting for Commander replenishment", result.stdout)

    def test_no_goal_requests_long_term_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            conn.close()

            result = run_cli("--root", str(root), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("waiting for a long-term goal", result.stdout)