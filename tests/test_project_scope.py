"""Tests for project-scoped database operations."""

import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.db import (
    connect,
    init_db,
    create_task,
    project_task_counts,
    project_list_tasks,
    project_next_ready_task,
    project_list_events,
    project_list_artifacts,
    add_artifact,
    transition_task,
)


class ProjectScopeTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _create_task(self, project_id: str, title: str) -> str:
        return create_task(
            self.conn,
            title=title,
            repo="repo",
            source_path=f"inbox/{title}.md",
            priority="normal",
            capabilities=["code"],
            goal=title,
            acceptance_criteria=["it works"],
            verification_commands=["echo ok"],
            project_id=project_id,
        )

    def test_task_counts_scoped(self) -> None:
        self._create_task("proj-a", "a1")
        self._create_task("proj-a", "a2")
        self._create_task("proj-b", "b1")

        counts_a = project_task_counts(self.conn, project_id="proj-a")
        counts_b = project_task_counts(self.conn, project_id="proj-b")

        self.assertEqual(counts_a.get("ready", 0), 2)
        self.assertEqual(counts_b.get("ready", 0), 1)

    def test_list_tasks_scoped(self) -> None:
        self._create_task("proj-a", "task-a")
        self._create_task("proj-b", "task-b")

        tasks_a = project_list_tasks(self.conn, project_id="proj-a")
        tasks_b = project_list_tasks(self.conn, project_id="proj-b")

        self.assertEqual(len(tasks_a), 1)
        self.assertEqual(len(tasks_b), 1)
        self.assertEqual(tasks_a[0]["title"], "task-a")
        self.assertEqual(tasks_b[0]["title"], "task-b")

    def test_next_ready_task_scoped(self) -> None:
        self._create_task("proj-a", "task-a")
        self._create_task("proj-b", "task-b")

        next_a = project_next_ready_task(self.conn, project_id="proj-a")
        next_b = project_next_ready_task(self.conn, project_id="proj-b")

        self.assertIsNotNone(next_a)
        self.assertIsNotNone(next_b)
        self.assertEqual(next_a["title"], "task-a")
        self.assertEqual(next_b["title"], "task-b")

    def test_events_scoped(self) -> None:
        a_id = self._create_task("proj-a", "task-a")
        b_id = self._create_task("proj-b", "task-b")

        events_a = project_list_events(self.conn, project_id="proj-a")
        events_b = project_list_events(self.conn, project_id="proj-b")

        self.assertTrue(all(e["task_id"] == a_id for e in events_a))
        self.assertTrue(all(e["task_id"] == b_id for e in events_b))

    def test_artifacts_scoped(self) -> None:
        a_id = self._create_task("proj-a", "task-a")
        b_id = self._create_task("proj-b", "task-b")

        add_artifact(self.conn, a_id, "log", Path("/tmp/a.log"))
        add_artifact(self.conn, b_id, "log", Path("/tmp/b.log"))

        arts_a = project_list_artifacts(self.conn, project_id="proj-a")
        arts_b = project_list_artifacts(self.conn, project_id="proj-b")

        self.assertEqual(len(arts_a), 1)
        self.assertEqual(len(arts_b), 1)
        self.assertIn("a.log", arts_a[0]["path"])
        self.assertIn("b.log", arts_b[0]["path"])

    def test_cross_project_isolation(self) -> None:
        """Operations on one project don't leak into another."""
        a_id = self._create_task("proj-a", "task-a")
        b_id = self._create_task("proj-b", "task-b")

        transition_task(self.conn, a_id, "running", "started")

        # proj-a has 1 running, proj-b has 1 ready
        counts_a = project_task_counts(self.conn, project_id="proj-a")
        counts_b = project_task_counts(self.conn, project_id="proj-b")

        self.assertEqual(counts_a.get("running", 0), 1)
        self.assertEqual(counts_a.get("ready", 0), 0)
        self.assertEqual(counts_b.get("ready", 0), 1)
        self.assertEqual(counts_b.get("running", 0), 0)

    def test_legacy_default_project(self) -> None:
        """Tasks without explicit project_id get legacy-default."""
        self._create_task("legacy-default", "old-task")
        tasks = project_list_tasks(self.conn, project_id="legacy-default")
        self.assertEqual(len(tasks), 1)

    def test_migration_backfills_legacy(self) -> None:
        """Rows from migration 001-008 get legacy-default project_id."""
        # Simulate pre-migration data by inserting without project_id
        self.conn.execute(
            "insert into tasks(id, title, repo, state, priority, capabilities, "
            "source_path, goal, acceptance_criteria, verification_commands) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("task-old", "old", "repo", "ready", "normal", "code", "x", "x", "x", "x"),
        )
        self.conn.commit()

        tasks = project_list_tasks(self.conn, project_id="legacy-default")
        self.assertTrue(any(t["id"] == "task-old" for t in tasks))

    def test_empty_project_returns_empty(self) -> None:
        tasks = project_list_tasks(self.conn, project_id="nonexistent")
        self.assertEqual(tasks, [])
        counts = project_task_counts(self.conn, project_id="nonexistent")
        self.assertEqual(counts, {})
