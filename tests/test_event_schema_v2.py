"""Red tests for Phase 6D supervisor event schema v2.

Owner: Grok (Phase 6D Task 0)
Expected before implementation: missing ``event_schema_v2`` module or table.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.supervisor_events import EventBroker
from tests.helpers import init_git_repo


class EventSchemaV2RedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.db_path = self.tmp / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.project_id = "proj-event-v2"
        self.broker = EventBroker()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_event_v2_mirrors_task_created_with_legacy_cursor(self) -> None:
        from local_cli_coordinator.event_schema_v2 import list_events_v2

        legacy_cursor = self.broker.publish(
            self.conn,
            self.project_id,
            "task_created",
            {"task_id": "task-abc", "title": "Mirror me"},
        )
        self.conn.commit()

        events = list_events_v2(self.conn, project_id=self.project_id)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["name"], "task.created")
        self.assertEqual(event["legacy_cursor"], legacy_cursor)
        self.assertEqual(event["project_id"], self.project_id)
        self.assertEqual(event["payload"]["task_id"], "task-abc")
        self.assertIn(event["severity"], {"debug", "info", "warn", "error"})
        self.assertIn(
            event["provenance"],
            {"supervisor", "commander", "worker", "evaluator", "operator", "tui", "cli"},
        )

    def test_event_v2_sequence_is_monotonic_per_project(self) -> None:
        from local_cli_coordinator.event_schema_v2 import list_events_v2

        self.broker.publish(self.conn, self.project_id, "task_started", {"task_id": "t1"})
        self.broker.publish(self.conn, self.project_id, "task_completed", {"task_id": "t1"})
        self.broker.publish(self.conn, "proj-other", "task_started", {"task_id": "t2"})
        self.conn.commit()

        events = list_events_v2(self.conn, project_id=self.project_id)
        sequences = [event["seq"] for event in events]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(set(sequences)), len(sequences))
        self.assertGreaterEqual(min(sequences), 1)