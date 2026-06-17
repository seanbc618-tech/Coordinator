import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, create_task, get_task, init_db, transition_task
from local_cli_coordinator.models import TASK_STATES


class ReviewStateTests(unittest.TestCase):
    def test_review_gate_states_are_valid_task_states(self) -> None:
        self.assertIn("reviewing_spec", TASK_STATES)
        self.assertIn("reviewing_quality", TASK_STATES)
        self.assertIn("awaiting_human", TASK_STATES)
        self.assertIn("rejected", TASK_STATES)

    def test_transition_task_accepts_review_gate_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Review feature",
                repo="demo",
                source_path="tasks/inbox/review.md",
                priority="normal",
                capabilities=["code"],
                goal="Review gates.",
                acceptance_criteria=["Review states work"],
                verification_commands=["python -m unittest"],
            )
            try:
                for state in (
                    "reviewing_spec",
                    "reviewing_quality",
                    "awaiting_human",
                    "rejected",
                ):
                    transition_task(conn, task_id, state, f"moved to {state}")
                    self.assertEqual(get_task(conn, task_id)["state"], state)
                with self.assertRaises(ValueError):
                    transition_task(conn, task_id, "reviewing_everything", "bad")
            finally:
                conn.close()

    def test_review_state_migration_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "coordinator.db")
            try:
                init_db(conn)

                row = conn.execute(
                    "select version from schema_migrations where version = ?",
                    ("002_review_states.sql",),
                ).fetchone()
            finally:
                conn.close()

        self.assertIsNotNone(row)
