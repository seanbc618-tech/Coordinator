import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.commander_memory import (
    COMMANDER_MEMORY_RELATIVE_PATH,
    commander_memory_path,
    goal_status_summary,
    write_commander_memory,
)
from local_cli_coordinator.db import connect, create_task, init_db, transition_task
from local_cli_coordinator.goals import (
    create_goal,
    finish_commander_run,
    link_task_to_goal,
    start_commander_run,
    transition_goal,
    update_goal_progress,
)


class CommanderMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_no_goal_status_summary(self) -> None:
        headline, detail = goal_status_summary(self.conn)
        self.assertEqual(headline, "Goal: none")
        self.assertEqual(detail, "waiting for a long-term goal")

    def test_active_empty_goal_waits_for_replenishment(self) -> None:
        goal_id = create_goal(
            self.conn,
            "Roadmap",
            "Finish roadmap",
            completion_criteria=[],
            constraints=[],
            repo_ids=["demo"],
        )
        transition_goal(self.conn, goal_id, "active")

        headline, detail = goal_status_summary(self.conn)
        self.assertEqual(headline, "Goal: active")
        self.assertEqual(detail, "waiting for Commander replenishment")

    def test_write_commander_memory_projects_goal_state(self) -> None:
        goal_id = create_goal(
            self.conn,
            "Roadmap",
            "Finish roadmap",
            completion_criteria=["dry-run"],
            constraints=["no secrets"],
            repo_ids=["demo"],
        )
        transition_goal(self.conn, goal_id, "active")
        update_goal_progress(self.conn, goal_id, "First slice ready")

        task_id = create_task(
            self.conn,
            title="Slice one",
            repo="demo",
            source_path="tasks/generated/slice-one.md",
            priority="normal",
            capabilities=["code"],
            goal="Ship slice one",
            acceptance_criteria=["Slice exists"],
            verification_commands=["python -m unittest"],
        )
        link_task_to_goal(
            self.conn,
            goal_id,
            task_id,
            batch_id="batch-1",
            proposal_fingerprint="fp-1",
            rationale="First slice",
        )
        transition_task(self.conn, task_id, "done", "completed")

        run_id = start_commander_run(
            self.conn,
            goal_id,
            "replenishment",
            1,
            Path("runs/commander/prompt.md"),
        )
        finish_commander_run(
            self.conn,
            run_id,
            status="succeeded",
            exit_code=0,
            timed_out=False,
            progress_summary="Ready for next slice",
        )

        path = write_commander_memory(self.conn, self.root, goal_id)

        self.assertEqual(path, commander_memory_path(self.root))
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("# Commander Memory", content)
        self.assertIn("Status: active", content)
        self.assertIn("Finish roadmap", content)
        self.assertIn("First slice ready", content)
        self.assertIn("Slice one: done", content)
        self.assertIn("## Next action", content)
        self.assertIn("waiting for Commander replenishment", content)
        self.assertIn("Latest run: succeeded", content)
        self.assertIn("Ready for next slice", content)
        self.assertEqual(COMMANDER_MEMORY_RELATIVE_PATH.as_posix(), "state/commander_memory.md")

    def test_write_commander_memory_escapes_control_characters(self) -> None:
        goal_id = create_goal(
            self.conn,
            "Roadmap",
            "Finish\x00roadmap",
            completion_criteria=[],
            constraints=[],
            repo_ids=["demo"],
        )
        path = write_commander_memory(self.conn, self.root, goal_id)
        content = path.read_text(encoding="utf-8")
        self.assertNotIn("\x00", content)
        self.assertIn("Finish roadmap", content)