"""Tests for supervisor event persistence and replay."""

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.supervisor_events import (
    EventBroker,
    EventEnvelope,
)


class EventBrokerTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.broker = EventBroker()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_publish_and_replay(self) -> None:
        self.broker.publish(self.conn, "proj-a", "task_started", {"task": "t1"})
        self.broker.publish(self.conn, "proj-a", "task_done", {"task": "t1"})

        events = self.broker.replay(self.conn, "proj-a", after=0)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_type, "task_started")
        self.assertEqual(events[1].event_type, "task_done")

    def test_per_project_cursors(self) -> None:
        self.broker.publish(self.conn, "proj-a", "e1", {})
        self.broker.publish(self.conn, "proj-b", "e2", {})
        self.broker.publish(self.conn, "proj-a", "e3", {})

        events_a = self.broker.replay(self.conn, "proj-a", after=0)
        events_b = self.broker.replay(self.conn, "proj-b", after=0)

        self.assertEqual(len(events_a), 2)
        self.assertEqual(len(events_b), 1)
        self.assertEqual(events_a[0].cursor, 1)
        self.assertEqual(events_a[1].cursor, 2)
        self.assertEqual(events_b[0].cursor, 1)

    def test_replay_after_cursor(self) -> None:
        self.broker.publish(self.conn, "proj-a", "e1", {})
        self.broker.publish(self.conn, "proj-a", "e2", {})
        self.broker.publish(self.conn, "proj-a", "e3", {})

        events = self.broker.replay(self.conn, "proj-a", after=2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "e3")

    def test_replay_with_limit(self) -> None:
        for i in range(5):
            self.broker.publish(self.conn, "proj-a", f"e{i}", {})

        events = self.broker.replay(self.conn, "proj-a", after=0, limit=3)
        self.assertEqual(len(events), 3)

    def test_empty_replay(self) -> None:
        events = self.broker.replay(self.conn, "proj-a", after=0)
        self.assertEqual(events, [])

    def test_subscriber_notification(self) -> None:
        received = []
        self.broker.subscribe("proj-a", lambda e: received.append(e))

        self.broker.publish(self.conn, "proj-a", "e1", {"x": 1})
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].event_type, "e1")

    def test_subscriber_only_for_subscribed_project(self) -> None:
        received = []
        self.broker.subscribe("proj-a", lambda e: received.append(e))

        self.broker.publish(self.conn, "proj-b", "e1", {})
        self.assertEqual(len(received), 0)

    def test_unsubscribe(self) -> None:
        received = []
        token = self.broker.subscribe("proj-a", lambda e: received.append(e))
        self.broker.unsubscribe(token)

        self.broker.publish(self.conn, "proj-a", "e1", {})
        self.assertEqual(len(received), 0)

    def test_payload_is_json(self) -> None:
        self.broker.publish(self.conn, "proj-a", "e1", {"key": "value"})
        events = self.broker.replay(self.conn, "proj-a", after=0)
        self.assertEqual(events[0].payload, {"key": "value"})

    def test_monotonic_cursors(self) -> None:
        for i in range(10):
            self.broker.publish(self.conn, "proj-a", f"e{i}", {})

        events = self.broker.replay(self.conn, "proj-a", after=0)
        cursors = [e.cursor for e in events]
        self.assertEqual(cursors, list(range(1, 11)))
