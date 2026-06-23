import sqlite3
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
            self.addCleanup(conn.close)

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

    def test_failed_migration_rolls_back_partial_ddl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            migrations_dir = tmp_path / "migrations"
            migrations_dir.mkdir()
            (migrations_dir / "001_broken.sql").write_text(
                "create table leaked(id integer);\n"
                "select * from missing_table;\n"
            )

            conn = connect(tmp_path / "coordinator.db")
            self.addCleanup(conn.close)

            with self.assertRaises(sqlite3.OperationalError):
                init_db(conn, migrations_root=migrations_dir)

            leaked = conn.execute(
                "select name from sqlite_master where type = 'table' and name = ?",
                ("leaked",),
            ).fetchone()
            self.assertIsNone(leaked)

            recorded = conn.execute(
                "select version from schema_migrations where version = ?",
                ("001_broken.sql",),
            ).fetchone()
            self.assertIsNone(recorded)
