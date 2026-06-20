"""Multi-project Supervisor loop.

Composes the scheduler, event broker, capacity enforcer, and method
registry into a single process. Uses a thread pool for concurrent
project execution. Integrates with the existing engine pipeline.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Generator

from .config import CoordinatorConfig
from .db import connect, init_db, project_next_ready_task, project_task_counts
from .project_runtime import ProjectRuntime, run_project_cycle
from .reporting import NULL_REPORTER, Reporter
from .runtime_paths import RuntimePaths
from .supervisor_capacity import SharedCapacity
from .supervisor_events import EventBroker
from .supervisor_methods import SupervisorMethods
from .supervisor_scheduler import FairProjectScheduler

log = logging.getLogger(__name__)


class MultiProjectSupervisor:
    """Manages multiple project loops under one process.

    Uses a thread pool for concurrent project execution. Each tick
    submits one project cycle to the pool if capacity allows.
    """

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        scheduler: FairProjectScheduler,
        broker: EventBroker,
        capacity: SharedCapacity,
        methods: SupervisorMethods,
        config: CoordinatorConfig,
        reporter: Reporter = NULL_REPORTER,
        max_workers: int = 4,
    ) -> None:
        self._paths = paths
        self._scheduler = scheduler
        self._broker = broker
        self._capacity = capacity
        self._methods = methods
        self._config = config
        self._reporter = reporter
        self._shutdown = threading.Event()
        self._paused: set[str] = set()
        self._stopped: set[str] = set()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._active_futures: dict[str, Future] = {}
        self._futures_lock = threading.Lock()

        # Expose paused/stopped sets to methods
        self._methods.set_paused_ref(self._paused)
        self._methods.set_stopped_ref(self._stopped)

    @contextmanager
    def _get_conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection as a context manager."""
        conn = connect(self._paths.database)
        init_db(conn)
        try:
            yield conn
        finally:
            conn.close()

    def tick(self) -> None:
        """Run one scheduler tick: pick a project, submit to worker pool.

        Respects pause/stop state and capacity limits.
        """
        if self._shutdown.is_set():
            return

        decision = self._scheduler.next(self._is_project_runnable)
        if decision is None:
            return

        project_id = decision.project_id
        task_key = f"task-{id(decision)}"

        # Acquire capacity with context-managed connection
        with self._get_conn() as conn:
            if not self._capacity.try_acquire(
                conn,
                project_id=project_id,
                task_id=task_key,
                agent_id="supervisor",
            ):
                return

            self._broker.publish(
                conn, project_id, "tick_scheduled",
                {"project_id": project_id, "reason": decision.reason},
            )

        # Submit to worker pool
        future = self._executor.submit(self._run_project_cycle, project_id, task_key)
        with self._futures_lock:
            self._active_futures[task_key] = future

        # Clean up completed futures
        with self._futures_lock:
            for key in list(self._active_futures):
                if self._active_futures[key].done():
                    del self._active_futures[key]

    def _run_project_cycle(self, project_id: str, task_key: str) -> None:
        """Run a project cycle in a worker thread."""
        with self._get_conn() as conn:
            try:
                runtime = ProjectRuntime(
                    project_id=project_id,
                    repo_root=self._paths.data_dir,
                    state_root=self._paths.state_dir,
                    config=self._config,
                )

                result = run_project_cycle(conn, runtime, self._reporter)

                self._broker.publish(
                    conn, project_id, "cycle_complete",
                    {
                        "tasks_processed": result.tasks_processed,
                        "failures": result.failures,
                        "stop_reason": result.stop_reason,
                        "task_id": result.task_id,
                    },
                )

                if result.failures > 0:
                    log.warning("project %s cycle failed: %s", project_id, result.stop_reason)
            finally:
                self._capacity.release(task_key)

    def _is_project_runnable(self, project_id: str) -> bool:
        """Check if a project is runnable: not paused/stopped, has ready
        tasks, and capacity is available."""
        if project_id in self._paused:
            return False
        if project_id in self._stopped:
            return False

        with self._get_conn() as conn:
            if self._capacity.active_count() >= self._executor._max_workers:
                return False
            next_task = project_next_ready_task(conn, project_id=project_id)
            return next_task is not None

    def pause_project(self, project_id: str) -> None:
        self._paused.add(project_id)

    def resume_project(self, project_id: str) -> None:
        self._paused.discard(project_id)

    def stop_project(self, project_id: str) -> None:
        """Permanently stop a project (until explicitly restarted)."""
        self._stopped.add(project_id)
        self._paused.discard(project_id)

    def restart_project(self, project_id: str) -> None:
        self._stopped.discard(project_id)

    def is_paused(self, project_id: str) -> bool:
        return project_id in self._paused

    def is_stopped(self, project_id: str) -> bool:
        return project_id in self._stopped

    def status(self) -> dict[str, Any]:
        """Return diagnostic status."""
        with self._get_conn() as conn:
            projects = {}
            rows = conn.execute(
                "select distinct project_id from tasks"
            ).fetchall()
            for row in rows:
                pid = row["project_id"]
                projects[pid] = project_task_counts(conn, project_id=pid)

            return {
                "projects": projects,
                "paused": sorted(self._paused),
                "stopped": sorted(self._stopped),
                "active_tasks": len(self._active_futures),
                "capacity_active": self._capacity.active_count(),
                "shutdown_requested": self._shutdown.is_set(),
            }

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def is_shutdown_requested(self) -> bool:
        return self._shutdown.is_set()

    def join_workers(self, timeout: float = 30.0) -> None:
        """Wait for all active workers to complete."""
        with self._futures_lock:
            futures = list(self._active_futures.values())
        for future in futures:
            try:
                future.result(timeout=timeout / max(len(futures), 1))
            except Exception:
                pass
