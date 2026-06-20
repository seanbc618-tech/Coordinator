"""Multi-project Supervisor loop.

Composes the scheduler, event broker, capacity enforcer, and method
registry into a single process. Uses a thread pool for concurrent
project execution.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .config import CoordinatorConfig
from .db import connect, init_db, project_next_ready_task, project_task_counts
from .project_runtime import ProjectRuntime, run_project_cycle, ProjectCycleResult
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
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._active_futures: dict[str, Any] = {}

        # Expose paused set to methods so API pause affects scheduling
        self._methods.set_paused_ref(self._paused)

    def tick(self) -> None:
        """Run one scheduler tick: pick a project, submit to worker pool.

        Respects pause state and capacity limits.
        """
        decision = self._scheduler.next(self._is_project_runnable)
        if decision is None:
            return

        project_id = decision.project_id

        # Acquire capacity
        if not self._capacity.try_acquire(
            self._get_conn(),
            project_id=project_id,
            task_id=f"tick-{project_id}",
            agent_id="supervisor",
        ):
            return

        # Publish tick event
        conn = self._get_conn()
        try:
            self._broker.publish(
                conn, project_id, "tick_scheduled",
                {"project_id": project_id, "reason": decision.reason},
            )
        finally:
            conn.close()

        # Submit to worker pool
        future = self._executor.submit(self._run_project_cycle, project_id)
        self._active_futures[project_id] = future

        # Clean up completed futures
        for pid in list(self._active_futures):
            if self._active_futures[pid].done():
                del self._active_futures[pid]

    def _run_project_cycle(self, project_id: str) -> ProjectCycleResult:
        """Run a project cycle in a worker thread."""
        conn = self._get_conn()
        try:
            # Find the repo path for this project from config
            # Use the first repo as default (multi-repo projects need registry)
            repo_root = self._paths.data_dir
            for repo in self._config.repos.values():
                repo_root = repo.path
                break

            runtime = ProjectRuntime(
                project_id=project_id,
                repo_root=repo_root,
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

            return result
        except Exception as exc:
            log.exception("project cycle failed for %s", project_id)
            self._broker.publish(
                conn, project_id, "cycle_error",
                {"error": str(exc)},
            )
            return ProjectCycleResult(
                project_id=project_id,
                failures=1,
                stop_reason=str(exc),
            )
        finally:
            self._capacity.release(f"tick-{project_id}")
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a fresh database connection."""
        conn = connect(self._paths.database)
        init_db(conn)
        return conn

    def _is_project_runnable(self, project_id: str) -> bool:
        """Check if a project is runnable: not paused, has ready tasks,
        and capacity is available."""
        if project_id in self._paused:
            return False

        if self._capacity.active_count() >= self._executor._max_workers:
            return False

        # Check for ready tasks
        conn = self._get_conn()
        try:
            next_task = project_next_ready_task(conn, project_id=project_id)
            return next_task is not None
        finally:
            conn.close()

    def pause_project(self, project_id: str) -> None:
        self._paused.add(project_id)

    def resume_project(self, project_id: str) -> None:
        self._paused.discard(project_id)

    def is_paused(self, project_id: str) -> bool:
        return project_id in self._paused

    def status(self) -> dict[str, Any]:
        """Return diagnostic status."""
        conn = self._get_conn()
        try:
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
                "active_tasks": len(self._active_futures),
                "capacity_active": self._capacity.active_count(),
                "shutdown_requested": self._shutdown.is_set(),
            }
        finally:
            conn.close()

    def request_shutdown(self) -> None:
        self._shutdown.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def is_shutdown_requested(self) -> bool:
        return self._shutdown.is_set()

    def join_workers(self, timeout: float = 5.0) -> None:
        """Wait for all active workers to complete."""
        self._executor.shutdown(wait=True)
