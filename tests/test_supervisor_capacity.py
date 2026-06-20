"""Tests for shared Supervisor capacity enforcement."""

import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.supervisor_capacity import (
    CapacityLease,
    SharedCapacity,
)


class SharedCapacityTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.capacity = SharedCapacity(
            max_global_running=4,
            max_per_project=2,
            max_daily_tasks=100,
        )

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_acquire_within_limits(self) -> None:
        result = self.capacity.try_acquire(
            self.conn, project_id="proj-a", task_id="t1", agent_id="agent-1"
        )
        self.assertTrue(result)

    def test_rejects_over_per_project_limit(self) -> None:
        self.capacity.try_acquire(self.conn, project_id="proj-a", task_id="t1", agent_id="a1")
        self.capacity.try_acquire(self.conn, project_id="proj-a", task_id="t2", agent_id="a2")
        result = self.capacity.try_acquire(self.conn, project_id="proj-a", task_id="t3", agent_id="a3")
        self.assertFalse(result)

    def test_rejects_over_global_limit(self) -> None:
        for i in range(4):
            pid = f"proj-{i}"
            self.capacity.try_acquire(self.conn, project_id=pid, task_id=f"t{i}", agent_id=f"a{i}")
        result = self.capacity.try_acquire(
            self.conn, project_id="proj-x", task_id="t-x", agent_id="a-x"
        )
        self.assertFalse(result)

    def test_release_frees_slot(self) -> None:
        self.capacity.try_acquire(self.conn, project_id="proj-a", task_id="t1", agent_id="a1")
        self.capacity.release("t1")
        result = self.capacity.try_acquire(
            self.conn, project_id="proj-a", task_id="t2", agent_id="a2"
        )
        self.assertTrue(result)

    def test_daily_budget(self) -> None:
        cap = SharedCapacity(max_global_running=100, max_per_project=100, max_daily_tasks=2)
        cap.try_acquire(self.conn, project_id="p", task_id="t1", agent_id="a1")
        cap.release("t1")
        cap.try_acquire(self.conn, project_id="p", task_id="t2", agent_id="a2")
        cap.release("t2")
        # Third task should be rejected (budget exhausted)
        result = cap.try_acquire(self.conn, project_id="p", task_id="t3", agent_id="a3")
        self.assertFalse(result)

    def test_active_count(self) -> None:
        self.capacity.try_acquire(self.conn, project_id="p", task_id="t1", agent_id="a1")
        self.capacity.try_acquire(self.conn, project_id="p", task_id="t2", agent_id="a2")
        self.assertEqual(self.capacity.active_count(), 2)
        self.assertEqual(self.capacity.active_count(project_id="p"), 2)
        self.assertEqual(self.capacity.active_count(project_id="q"), 0)

    def test_release_nonexistent_is_noop(self) -> None:
        self.capacity.release("nonexistent")  # Should not raise
