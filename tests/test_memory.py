import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, create_task, get_task, init_db
from local_cli_coordinator.engine import run_one_ready_task
from local_cli_coordinator.memory import (
    LoopMemoryEntry,
    append_loop_memory,
    loop_memory_path,
)
from tests.helpers import init_git_repo, run_cli
from tests.test_engine import test_config


class LoopMemoryTests(unittest.TestCase):
    def test_append_loop_memory_entry_creates_readable_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            append_loop_memory(
                root,
                LoopMemoryEntry(
                    task_id="task-123",
                    repo="demo",
                    title="Create feature file",
                    outcome="done",
                    branch="coord/task-123-create-feature-file",
                    verifier_result="passed",
                    next_action="continue next task",
                ),
            )

            text = loop_memory_path(root).read_text()

        self.assertIn("# Loop State", text)
        self.assertIn("task-123", text)
        self.assertIn("repo: demo", text)
        self.assertIn("title: Create feature file", text)
        self.assertIn("outcome: done", text)
        self.assertIn("branch: coord/task-123-create-feature-file", text)
        self.assertIn("verifier: passed", text)
        self.assertIn("next action: continue next task", text)

    def test_engine_appends_loop_memory_after_processed_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Create feature file",
                repo="demo",
                source_path="tasks/inbox/feature.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains done"],
                verification_commands=[],
            )
            try:
                processed = run_one_ready_task(conn, test_config(repo), root)
                task = get_task(conn, task_id)
            finally:
                conn.close()

            text = (root / "state" / "loop_state.md").read_text()

        self.assertTrue(processed)
        self.assertEqual(task["state"], "done")
        self.assertIn(task_id, text)
        self.assertIn("repo: demo", text)
        self.assertIn("title: Create feature file", text)
        self.assertIn("outcome: done", text)
        self.assertIn("branch: coord/", text)
        self.assertIn("verifier: passed", text)
        self.assertIn("next action: continue next task", text)

    def test_status_shows_loop_memory_path_when_it_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_path = root / "state" / "loop_state.md"
            memory_path.parent.mkdir(parents=True)
            memory_path.write_text("# Loop State\n")

            result = run_cli("--root", str(root), "status")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("loop memory: state/loop_state.md", result.stdout)
