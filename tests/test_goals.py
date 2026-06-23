"""Tests for commander goal persistence."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import (
    add_commander_message,
    clear_commander_failures,
    create_goal,
    finish_commander_run,
    get_goal,
    active_goal,
    active_goal_for_project,
    goal_for_task,
    link_task_to_goal,
    linked_task_counts,
    list_commander_messages,
    list_commander_runs,
    list_linked_tasks,
    record_commander_failure,
    start_commander_run,
    transition_goal,
    update_goal_progress,
    get_latest_commander_run,
)
from local_cli_coordinator.db import create_task


class GoalPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    # --- Goal creation and uniqueness ---

    def test_create_goal_returns_id(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        self.assertIsInstance(goal_id, int)
        self.assertGreater(goal_id, 0)

    def test_goal_defaults_to_draft(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        goal = get_goal(self.conn, goal_id)
        self.assertEqual(goal["status"], "draft")
        self.assertEqual(goal["title"], "Roadmap")
        self.assertEqual(goal["objective"], "Finish roadmap")

    def test_only_one_nonterminal_goal_is_allowed(self) -> None:
        create_goal(
            self.conn,
            "Roadmap",
            "Finish roadmap",
            completion_criteria=["dry-run"],
            constraints=["demo"],
        )
        with self.assertRaises(sqlite3.IntegrityError):
            create_goal(
                self.conn,
                "Other",
                "Do other work",
                constraints=["demo"],
            )

    def test_terminal_goal_allows_new_goal(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        transition_goal(self.conn, goal_id, "completed")
        new_id = create_goal(self.conn, "Other", "Do other work")
        self.assertNotEqual(goal_id, new_id)

    def test_abandoned_goal_allows_new_goal(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        transition_goal(self.conn, goal_id, "abandoned")
        new_id = create_goal(self.conn, "Other", "Do other work")
        self.assertNotEqual(goal_id, new_id)

    # --- Goal transitions ---

    def test_transition_goal_updates_status(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        transition_goal(self.conn, goal_id, "active")
        goal = get_goal(self.conn, goal_id)
        self.assertEqual(goal["status"], "active")
        self.assertIsNotNone(goal["confirmed_at"])

    def test_transition_to_invalid_state_raises(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        with self.assertRaises(ValueError):
            transition_goal(self.conn, goal_id, "invalid_state")

    def test_transition_to_completed_sets_timestamp(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        transition_goal(self.conn, goal_id, "completed")
        goal = get_goal(self.conn, goal_id)
        self.assertIsNotNone(goal["completed_at"])

    def test_transition_to_paused_sets_timestamp(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        transition_goal(self.conn, goal_id, "active")
        transition_goal(self.conn, goal_id, "paused")
        goal = get_goal(self.conn, goal_id)
        self.assertIsNotNone(goal["paused_at"])

    def test_transition_with_stop_reason(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        transition_goal(self.conn, goal_id, "blocked", stop_reason="waiting for API key")
        goal = get_goal(self.conn, goal_id)
        self.assertEqual(goal["stop_reason"], "waiting for API key")

    # --- Active goal query ---

    def test_active_goal_returns_draft(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        self.assertEqual(active_goal(self.conn)["id"], goal_id)

    def test_active_goal_returns_none_when_all_terminal(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        transition_goal(self.conn, goal_id, "completed")
        self.assertIsNone(active_goal(self.conn))

    # --- Progress ---

    def test_update_goal_progress(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        update_goal_progress(self.conn, goal_id, "50% done")
        goal = get_goal(self.conn, goal_id)
        self.assertEqual(goal["progress_summary"], "50% done")

    # --- Messages ---

    def test_add_and_list_messages(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        add_commander_message(self.conn, goal_id, "user", "Begin")
        add_commander_message(self.conn, goal_id, "assistant", "Starting plan")
        messages = list_commander_messages(self.conn, goal_id)
        self.assertEqual(len(messages), 2)
        # Most recent first
        self.assertEqual(messages[0]["role"], "assistant")
        self.assertEqual(messages[1]["role"], "user")

    # --- Commander runs ---

    def test_start_and_finish_commander_run(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        run_id = start_commander_run(
            self.conn, goal_id, "initial_plan", 1, Path("/tmp/prompt.md")
        )
        self.assertIsInstance(run_id, int)

        finish_commander_run(
            self.conn, run_id,
            status="succeeded", exit_code=0, timed_out=False,
        )
        run = get_latest_commander_run(self.conn, goal_id)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["exit_code"], 0)
        self.assertEqual(run["timed_out"], 0)

    def test_get_latest_commander_run_returns_none(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        self.assertIsNone(get_latest_commander_run(self.conn, goal_id))

    def test_list_commander_runs_chronological(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        run1 = start_commander_run(self.conn, goal_id, "initial_plan", 1, Path("/tmp/p1.md"))
        finish_commander_run(self.conn, run1, status="succeeded")
        run2 = start_commander_run(self.conn, goal_id, "replenishment", 1, Path("/tmp/p2.md"))
        finish_commander_run(self.conn, run2, status="succeeded")
        runs = list_commander_runs(self.conn, goal_id)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["id"], run1)
        self.assertEqual(runs[1]["id"], run2)

    # --- Task-goal links ---

    def test_link_task_and_query(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap", repo_ids=["demo"])
        task_id = create_task(
            self.conn,
            title="Add parser",
            repo="demo",
            source_path="tasks/inbox/parser.md",
            priority="normal",
            capabilities=["code"],
            goal="Add parser",
            acceptance_criteria=["parser works"],
            verification_commands=["python -m pytest"],
        )
        link_task_to_goal(self.conn, goal_id, task_id, "batch-1", "fp-1", "Advances goal")
        self.assertEqual(goal_for_task(self.conn, task_id)["id"], goal_id)

    def test_linked_task_counts(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap", repo_ids=["demo"])
        t1 = create_task(
            self.conn, title="T1", repo="demo", source_path="",
            priority="normal", capabilities=["code"], goal="G",
            acceptance_criteria=["A"], verification_commands=[],
        )
        t2 = create_task(
            self.conn, title="T2", repo="demo", source_path="",
            priority="normal", capabilities=["code"], goal="G",
            acceptance_criteria=["A"], verification_commands=[],
        )
        link_task_to_goal(self.conn, goal_id, t1)
        link_task_to_goal(self.conn, goal_id, t2)
        counts = linked_task_counts(self.conn, goal_id)
        self.assertEqual(counts.get("ready", 0), 2)

    def test_list_linked_tasks(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap", repo_ids=["demo"])
        t1 = create_task(
            self.conn, title="T1", repo="demo", source_path="",
            priority="normal", capabilities=["code"], goal="G",
            acceptance_criteria=["A"], verification_commands=[],
        )
        link_task_to_goal(self.conn, goal_id, t1, "batch-1", "fp-1", "reason")
        tasks = list_linked_tasks(self.conn, goal_id)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["title"], "T1")
        self.assertEqual(tasks[0]["batch_id"], "batch-1")

    # --- Failure tracking ---

    def test_record_and_clear_failures(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        record_commander_failure(self.conn, goal_id)
        goal = get_goal(self.conn, goal_id)
        self.assertEqual(goal["commander_failures"], 1)
        self.assertNotEqual(goal["commander_retry_after"], "")

        clear_commander_failures(self.conn, goal_id)
        goal = get_goal(self.conn, goal_id)
        self.assertEqual(goal["commander_failures"], 0)
        self.assertEqual(goal["commander_retry_after"], "")

    def test_failure_backoff_escalates(self) -> None:
        goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap")
        record_commander_failure(self.conn, goal_id)
        goal1 = get_goal(self.conn, goal_id)
        record_commander_failure(self.conn, goal_id)
        goal2 = get_goal(self.conn, goal_id)
        self.assertEqual(goal1["commander_failures"], 1)
        self.assertEqual(goal2["commander_failures"], 2)

    # --- Full round trip ---

    def test_run_message_and_task_link_round_trip(self) -> None:
        goal_id = create_goal(
            self.conn, "Roadmap", "Finish roadmap", constraints=["demo"]
        )
        task_id = create_task(
            self.conn,
            title="Add parser",
            repo="demo",
            source_path="tasks/inbox/parser.md",
            priority="normal",
            capabilities=["code"],
            goal="Add parser",
            acceptance_criteria=["parser works"],
            verification_commands=["python -m pytest"],
        )
        add_commander_message(self.conn, goal_id, "user", "Begin")
        run_id = start_commander_run(
            self.conn, goal_id, "initial_plan", 1, Path("prompt.md")
        )
        finish_commander_run(
            self.conn, run_id, status="succeeded", exit_code=0, timed_out=False
        )
        link_task_to_goal(
            self.conn, goal_id, task_id, "batch-1", "fp-1", "Advances goal"
        )
        self.assertEqual(
            get_latest_commander_run(self.conn, goal_id)["status"], "succeeded"
        )
        self.assertEqual(goal_for_task(self.conn, task_id)["id"], goal_id)


class ProjectScopedGoalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "coordinator.db")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_two_projects_each_get_draft_goal(self) -> None:
        goal_a = create_goal(
            self.conn, "Goal A", "Objective A", project_id="proj-a"
        )
        goal_b = create_goal(
            self.conn, "Goal B", "Objective B", project_id="proj-b"
        )
        self.assertNotEqual(goal_a, goal_b)
        self.assertEqual(active_goal_for_project(self.conn, "proj-a")["id"], goal_a)
        self.assertEqual(active_goal_for_project(self.conn, "proj-b")["id"], goal_b)

    def test_second_nonterminal_goal_same_project_fails(self) -> None:
        create_goal(self.conn, "First", "First objective", project_id="proj-a")
        with self.assertRaises(sqlite3.IntegrityError):
            create_goal(self.conn, "Second", "Second objective", project_id="proj-a")

    def test_terminal_goal_allows_new_goal_same_project(self) -> None:
        goal_id = create_goal(self.conn, "First", "First objective", project_id="proj-a")
        transition_goal(self.conn, goal_id, "completed")
        new_id = create_goal(self.conn, "Second", "Second objective", project_id="proj-a")
        self.assertNotEqual(goal_id, new_id)

    def test_active_goal_delegates_legacy_default(self) -> None:
        goal_id = create_goal(self.conn, "Legacy", "Legacy objective")
        self.assertEqual(active_goal(self.conn)["id"], goal_id)
        self.assertEqual(
            active_goal_for_project(self.conn, "legacy-default")["id"], goal_id
        )


class GoalMigrationTests(unittest.TestCase):
    """Test that the migration applies cleanly."""

    def test_migration_creates_commander_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "coordinator.db"
            conn = connect(db_path)
            init_db(conn)
            tables = {
                row[0]
                for row in conn.execute(
                    "select name from sqlite_master where type = 'table'"
                ).fetchall()
            }
            conn.close()
            self.assertIn("goals", tables)
            self.assertIn("commander_runs", tables)
            self.assertIn("commander_messages", tables)
            self.assertIn("task_goal_links", tables)


if __name__ == "__main__":
    unittest.main()
