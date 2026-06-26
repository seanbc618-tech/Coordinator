import multiprocessing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig
from local_cli_coordinator.db import (
    MIGRATIONS_DIR,
    acquire_task_lease,
    active_lease_count,
    claim_next_ready_task,
    connect,
    create_task,
    init_db,
    release_task_lease,
)
from local_cli_coordinator.engine import _claim_next_ready_task


def _db(root: Path):
    conn = connect(root / "coordinator.db")
    init_db(conn)
    return conn


def _create_ready_task(conn, title="Test task"):
    return create_task(
        conn,
        title=title,
        repo="demo",
        source_path="tasks/inbox/test.md",
        priority="normal",
        capabilities=["code"],
        goal="Do something.",
        acceptance_criteria=["Done."],
        verification_commands=[],
    )


class AcquireLeaseTests(unittest.TestCase):
    def test_acquire_lease_on_unleased_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            task_id = _create_ready_task(conn)
            result = acquire_task_lease(conn, task_id, "agent-1")
            conn.close()

        self.assertTrue(result)

    def test_acquire_lease_on_already_leased_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            task_id = _create_ready_task(conn)
            acquire_task_lease(conn, task_id, "agent-1")
            result = acquire_task_lease(conn, task_id, "agent-2")
            conn.close()

        self.assertFalse(result)

    def test_acquire_lease_after_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            task_id = _create_ready_task(conn)
            acquire_task_lease(conn, task_id, "agent-1")
            release_task_lease(conn, task_id)
            result = acquire_task_lease(conn, task_id, "agent-2")
            conn.close()

        self.assertTrue(result)


class ReleaseLeaseTests(unittest.TestCase):
    def test_release_removes_active_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            task_id = _create_ready_task(conn)
            acquire_task_lease(conn, task_id, "agent-1")
            release_task_lease(conn, task_id)
            count = active_lease_count(conn, "agent-1")
            conn.close()

        self.assertEqual(count, 0)

    def test_release_nonexistent_lease_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            _create_ready_task(conn)
            # Should not raise
            release_task_lease(conn, "nonexistent")
            conn.close()


class ActiveLeaseCountTests(unittest.TestCase):
    def test_counts_active_leases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            t1 = _create_ready_task(conn, "Task 1")
            t2 = _create_ready_task(conn, "Task 2")
            acquire_task_lease(conn, t1, "agent-1")
            acquire_task_lease(conn, t2, "agent-1")
            count = active_lease_count(conn, "agent-1")
            conn.close()

        self.assertEqual(count, 2)

    def test_global_count_without_agent_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            t1 = _create_ready_task(conn, "Task 1")
            t2 = _create_ready_task(conn, "Task 2")
            acquire_task_lease(conn, t1, "agent-1")
            acquire_task_lease(conn, t2, "agent-2")
            count = active_lease_count(conn)
            conn.close()

        self.assertEqual(count, 2)


def _parallel_acquire(db_path: str, task_id: str, agent_id: str, queue) -> None:
    conn = connect(Path(db_path))
    init_db(conn)
    queue.put(acquire_task_lease(conn, task_id, agent_id))
    conn.close()


class LeaseMigrationTests(unittest.TestCase):
    def test_migration_dedupes_duplicate_active_leases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            partial_migrations = root / "partial_migrations"
            partial_migrations.mkdir()
            # Apply all migrations except 005 (which dedupes leases)
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if migration.name.startswith("005"):
                    continue
                partial_migrations.joinpath(migration.name).write_text(
                    migration.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            conn = connect(root / "coordinator.db")
            init_db(conn, partial_migrations)
            task_id = _create_ready_task(conn)
            conn.execute(
                "insert into task_leases(task_id, agent_id, expires_at) values (?, ?, ?)",
                (task_id, "agent-1", "2099-01-01T00:00:00+00:00"),
            )
            conn.execute(
                "insert into task_leases(task_id, agent_id, expires_at) values (?, ?, ?)",
                (task_id, "agent-2", "2099-01-01T00:00:00+00:00"),
            )
            conn.commit()

            init_db(conn, MIGRATIONS_DIR)
            active = conn.execute(
                "select count(*) as cnt from task_leases where task_id = ? and released_at is null",
                (task_id,),
            ).fetchone()["cnt"]
            conn.close()

        self.assertEqual(active, 1)


class CapabilityLeaseTests(unittest.TestCase):
    def test_claim_records_lease_for_capability_matched_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = _db(root)
            task_id = create_task(
                conn,
                title="Docs task",
                repo="demo",
                source_path="tasks/inbox/docs.md",
                priority="normal",
                capabilities=["docs"],
                goal="Write docs.",
                acceptance_criteria=["Done."],
                verification_commands=[],
            )
            config = CoordinatorConfig(
                agents={
                    "code-worker": AgentConfig(
                        id="code-worker",
                        command="echo",
                        capabilities=["code"],
                        max_concurrency=1,
                        role="worker",
                    ),
                    "docs-worker": AgentConfig(
                        id="docs-worker",
                        command="echo",
                        capabilities=["docs"],
                        max_concurrency=1,
                        role="worker",
                    ),
                },
                repos={},
                policy=PolicyConfig(
                    require_single_repo=True,
                    require_acceptance_criteria=True,
                    require_verification_commands=False,
                    require_handoff_summary=False,
                    max_files_touched=3,
                    max_expected_minutes=30,
                    max_attempts=3,
                    split_if_touches_multiple_subsystems=True,
                    split_if_research_and_code_are_mixed=True,
                ),
            )

            task, agent_id = _claim_next_ready_task(conn, config)
            lease = conn.execute(
                "select agent_id from task_leases where task_id = ? and released_at is null",
                (task_id,),
            ).fetchone()
            conn.close()

        self.assertIsNotNone(task)
        self.assertEqual(agent_id, "docs-worker")
        self.assertEqual(lease["agent_id"], "docs-worker")


class ConcurrentLeaseTests(unittest.TestCase):
    def test_only_one_connection_acquires_task_under_contention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = _db(root)
            task_id = _create_ready_task(conn)
            conn.close()

            queue: multiprocessing.Queue = multiprocessing.Queue()
            workers = [
                multiprocessing.Process(
                    target=_parallel_acquire,
                    args=(str(root / "coordinator.db"), task_id, "agent-1", queue),
                ),
                multiprocessing.Process(
                    target=_parallel_acquire,
                    args=(str(root / "coordinator.db"), task_id, "agent-2", queue),
                ),
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

            results = [queue.get_nowait() for _ in workers]

        self.assertEqual(sum(results), 1)


class ClaimNextReadyTaskTests(unittest.TestCase):
    def test_claims_first_ready_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            task_id = _create_ready_task(conn, "First task")
            claimed = claim_next_ready_task(conn, "agent-1")
            conn.close()

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], task_id)

    def test_returns_none_when_no_ready_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            claimed = claim_next_ready_task(conn, "agent-1")
            conn.close()

        self.assertIsNone(claimed)

    def test_respects_agent_concurrency_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            _create_ready_task(conn, "Task 1")
            _create_ready_task(conn, "Task 2")
            claim_next_ready_task(conn, "agent-1", max_agent_concurrency=1)
            second = claim_next_ready_task(conn, "agent-1", max_agent_concurrency=1)
            conn.close()

        self.assertIsNone(second)

    def test_respects_global_concurrency_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            _create_ready_task(conn, "Task 1")
            _create_ready_task(conn, "Task 2")
            claim_next_ready_task(conn, "agent-1", max_global_concurrency=1)
            second = claim_next_ready_task(conn, "agent-2", max_global_concurrency=1)
            conn.close()

        self.assertIsNone(second)

    def test_different_agents_can_claim_different_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _db(Path(tmp))
            t1 = _create_ready_task(conn, "Task 1")
            t2 = _create_ready_task(conn, "Task 2")
            c1 = claim_next_ready_task(conn, "agent-1", max_agent_concurrency=1)
            c2 = claim_next_ready_task(conn, "agent-2", max_agent_concurrency=1)
            conn.close()

        self.assertIsNotNone(c1)
        self.assertIsNotNone(c2)
        self.assertNotEqual(c1["id"], c2["id"])
