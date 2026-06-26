"""Tests for supervisor event persistence and replay."""

import json
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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

    def test_unsubscribe_out_of_order_clears_all_subscribers(self) -> None:
        tokens = [
            self.broker.subscribe("proj-a", lambda e: None)
            for _ in range(3)
        ]
        self.broker.unsubscribe(tokens[0])
        self.broker.unsubscribe(tokens[2])
        self.broker.unsubscribe(tokens[1])
        self.assertEqual(len(self.broker._subscribers), 0)  # noqa: SLF001

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

    def test_concurrent_publish_allocates_unique_cursors(self) -> None:
        thread_count = 12
        errors: list[Exception] = []
        barrier = threading.Barrier(thread_count)

        def publish_once(i: int) -> int:
            conn = connect(self.db_path)
            init_db(conn)
            try:
                barrier.wait(timeout=5.0)
                return self.broker.publish(conn, "proj-a", f"e{i}", {"i": i})
            except Exception as exc:
                errors.append(exc)
                raise
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=thread_count) as pool:
            cursors = list(pool.map(publish_once, range(thread_count)))

        self.assertEqual(errors, [])
        self.assertEqual(len(cursors), thread_count)
        self.assertEqual(len(set(cursors)), thread_count)

        events = self.broker.replay(self.conn, "proj-a", after=0)
        self.assertEqual(len(events), thread_count)

    def test_publish_during_unsubscribe_does_not_crash(self) -> None:
        tokens = [self.broker.subscribe("proj-a", lambda e: None) for _ in range(3)]
        stop = threading.Event()
        errors: list[Exception] = []

        def unsubscribe_loop() -> None:
            try:
                for _ in range(80):
                    if stop.is_set():
                        break
                    for token in tokens:
                        self.broker.unsubscribe(token)
                    for i in range(len(tokens)):
                        tokens[i] = self.broker.subscribe("proj-a", lambda e: None)
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=unsubscribe_loop, daemon=True)
        thread.start()
        try:
            for i in range(50):
                self.broker.publish(self.conn, "proj-a", f"e{i}", {})
        finally:
            stop.set()
            thread.join(timeout=2.0)

        self.assertEqual(errors, [])
