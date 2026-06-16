import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, create_task, get_task, init_db, transition_task


class DatabaseTests(unittest.TestCase):
    def test_create_and_transition_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "coordinator.db"
            conn = connect(db_path)
            init_db(conn)

            task_id = create_task(
                conn,
                title="Add parser regression",
                repo="polymarket-weather-arb",
                source_path="tasks/inbox/parser.md",
                priority="normal",
                capabilities=["tests", "code"],
                goal="Add focused regression coverage.",
                acceptance_criteria=["pytest passes"],
                verification_commands=["uv run pytest tests/test_rules.py -q"],
            )

            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "ready")
            self.assertEqual(task["repo"], "polymarket-weather-arb")
            self.assertEqual(task["capabilities"], "tests,code")

            transition_task(conn, task_id, "running", "agent started")
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "running")

            events = conn.execute(
                "select new_state, note from events where task_id = ? order by id",
                (task_id,),
            ).fetchall()
            self.assertEqual(events[-1]["new_state"], "running")
            self.assertEqual(events[-1]["note"], "agent started")
