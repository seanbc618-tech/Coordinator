import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.config import CoordinatorConfig, PolicyConfig
from local_cli_coordinator.cli import _cmd_daemon
from local_cli_coordinator.db import (
    circuit_breaker_reason,
    connect,
    create_task,
    finish_daemon_run,
    init_db,
    start_daemon_run,
)
from local_cli_coordinator.commander_service import maybe_replenish_goal
from local_cli_coordinator.engine import run_one_ready_task
from local_cli_coordinator.goals import create_goal, get_goal, transition_goal
from tests.helpers import run_cli
from tests.test_cli_commands import write_config
from tests.test_commander_replenishment import _test_config


def policy(**overrides) -> PolicyConfig:
    values = dict(
        require_single_repo=True,
        require_acceptance_criteria=True,
        require_verification_commands=True,
        require_handoff_summary=True,
        max_files_touched=3,
        max_expected_minutes=30,
        max_attempts=3,
        split_if_touches_multiple_subsystems=True,
        split_if_research_and_code_are_mixed=True,
    )
    values.update(overrides)
    return PolicyConfig(**values)


def ready_task(conn) -> str:
    return create_task(
        conn,
        title="Ready",
        repo="demo",
        source_path="tasks/inbox/ready.md",
        priority="normal",
        capabilities=["code"],
        goal="Wait for capacity.",
        acceptance_criteria=["Remains ready."],
        verification_commands=["true"],
    )


class CircuitBreakerTests(unittest.TestCase):
    def test_run_ledger_records_completion_counts_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "coordinator.db")
            init_db(conn)
            run_id = start_daemon_run(conn)
            finish_daemon_run(
                conn,
                run_id,
                tasks_processed=2,
                failures=1,
                stop_reason="max tasks per run reached",
            )
            row = conn.execute("select * from daemon_runs where id = ?", (run_id,)).fetchone()
            conn.close()

        self.assertIsNotNone(row["started_at"])
        self.assertIsNotNone(row["ended_at"])
        self.assertEqual(row["tasks_processed"], 2)
        self.assertEqual(row["failures"], 1)
        self.assertEqual(row["stop_reason"], "max tasks per run reached")

    def test_daily_task_cap_refuses_ready_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = ready_task(conn)
            run_id = start_daemon_run(conn)
            finish_daemon_run(conn, run_id, tasks_processed=2, failures=0)
            config = CoordinatorConfig(agents={}, repos={}, policy=policy(max_tasks_per_day=2))

            processed = run_one_ready_task(conn, config, root)
            state = conn.execute("select state from tasks where id = ?", (task_id,)).fetchone()["state"]
            reason = circuit_breaker_reason(conn, config.policy)
            conn.close()

        self.assertFalse(processed)
        self.assertEqual(state, "ready")
        self.assertIn("daily task limit reached", reason)

    def test_consecutive_failure_cap_refuses_ready_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = ready_task(conn)
            for _ in range(2):
                run_id = start_daemon_run(conn)
                finish_daemon_run(conn, run_id, tasks_processed=1, failures=1)
            config = CoordinatorConfig(
                agents={},
                repos={},
                policy=policy(max_tasks_per_day=99, max_consecutive_failures=2),
            )

            processed = run_one_ready_task(conn, config, root)
            reason = circuit_breaker_reason(conn, config.policy)
            state = conn.execute("select state from tasks where id = ?", (task_id,)).fetchone()["state"]
            conn.close()

        self.assertFalse(processed)
        self.assertEqual(state, "ready")
        self.assertIn("consecutive failure limit reached", reason)

    def test_idle_run_does_not_reset_consecutive_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "coordinator.db")
            init_db(conn)
            for tasks_processed, failures in ((1, 1), (0, 0), (1, 1)):
                run_id = start_daemon_run(conn)
                finish_daemon_run(
                    conn,
                    run_id,
                    tasks_processed=tasks_processed,
                    failures=failures,
                )
            reason = circuit_breaker_reason(
                conn,
                policy(max_tasks_per_day=99, max_consecutive_failures=2),
            )
            conn.close()

        self.assertIn("consecutive failure limit reached", reason)

    def test_daemon_cli_reports_circuit_breaker_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            run_id = start_daemon_run(conn)
            finish_daemon_run(conn, run_id, tasks_processed=24, failures=0)
            conn.close()

            result = run_cli("--root", str(root), "daemon", "--once")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stopped: daily task limit reached", result.stdout)

    def test_commander_failures_do_not_trip_task_circuit_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            goal_id = create_goal(conn, "Roadmap", "Finish roadmap", repo_ids=["demo"])
            transition_goal(conn, goal_id, "active")
            config = _test_config(root, command="python3 -c 'import sys; sys.exit(1)'")

            maybe_replenish_goal(conn, config, root)
            reason = circuit_breaker_reason(conn, config.policy)
            goal = get_goal(conn, goal_id)
            conn.close()

        self.assertIsNone(reason)
        self.assertEqual(goal["commander_failures"], 1)

    def test_daemon_exception_still_finishes_run_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            args = argparse.Namespace(root=str(root), db="coordinator.db", once=True)

            with (
                patch(
                    "local_cli_coordinator.cli.run_daemon_cycle",
                    side_effect=RuntimeError("boom"),
                ),
                self.assertRaisesRegex(RuntimeError, "boom"),
            ):
                _cmd_daemon(args)

            conn = connect(root / "coordinator.db")
            row = conn.execute("select * from daemon_runs order by id desc limit 1").fetchone()
            conn.close()

        self.assertIsNotNone(row["ended_at"])
        self.assertEqual(row["failures"], 1)
        self.assertIn("daemon error: RuntimeError: boom", row["stop_reason"])
