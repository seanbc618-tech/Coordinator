import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import add_artifact, connect, create_task, init_db, transition_task
from tests.helpers import run_cli


def _setup_db(root: Path) -> None:
    conn = connect(root / "coordinator.db")
    init_db(conn)
    task_id = create_task(
        conn,
        title="Test task",
        repo="demo",
        source_path="tasks/inbox/test.md",
        priority="normal",
        capabilities=["code"],
        goal="Do something.",
        acceptance_criteria=["Done."],
        verification_commands=[],
    )
    transition_task(conn, task_id, "running", "assigned to agent-1")
    transition_task(conn, task_id, "done", "completed")
    add_artifact(conn, task_id, "verifier_log", root / "runs" / "verifier.log")
    conn.close()


class TaskEventsTests(unittest.TestCase):
    def test_task_events_unknown_id_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _setup_db(root)
            result = run_cli("--root", str(root), "task", "events", "nonexistent")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown task", result.stdout)

    def test_task_events_shows_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _setup_db(root)
            # Get the task id
            conn = connect(root / "coordinator.db")
            init_db(conn)
            rows = conn.execute("select id from tasks").fetchall()
            task_id = rows[0]["id"]
            conn.close()

            result = run_cli("--root", str(root), "task", "events", task_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ready", result.stdout)
        self.assertIn("running", result.stdout)
        self.assertIn("done", result.stdout)


class TaskArtifactsTests(unittest.TestCase):
    def test_task_artifacts_unknown_id_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _setup_db(root)
            result = run_cli("--root", str(root), "task", "artifacts", "nonexistent")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown task", result.stdout)

    def test_task_artifacts_shows_kind_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _setup_db(root)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            rows = conn.execute("select id from tasks").fetchall()
            task_id = rows[0]["id"]
            conn.close()

            result = run_cli("--root", str(root), "task", "artifacts", task_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verifier_log:", result.stdout)
