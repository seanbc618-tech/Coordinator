"""Multi-project Supervisor loop.

Composes the scheduler, event broker, capacity enforcer, and method
registry into a single process that serves multiple project loops
over a Unix socket.
"""

from __future__ import annotations

import threading
from typing import Any

from .config import CoordinatorConfig
from .db import (
    connect,
    init_db,
    project_next_ready_task,
    project_task_counts,
)
from .project_runtime import ProjectRuntime, run_project_cycle
from .reporting import NULL_REPORTER, Reporter
from .runtime_paths import RuntimePaths
from .supervisor_capacity import SharedCapacity
from .supervisor_events import EventBroker
from .supervisor_methods import SupervisorMethods
from .supervisor_scheduler import FairProjectScheduler


class MultiProjectSupervisor:
    """Manages multiple project loops under one process.

    Owns the scheduler tick loop, worker executor, and graceful shutdown.
    The socket server is managed externally (by the CLI or caller).
    """

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        scheduler: FairProjectScheduler,
        broker: EventBroker,
        capacity: SharedCapacity,
        methods: SupervisorMethods,
        config: CoordinatorConfig | None = None,
        reporter: Reporter = NULL_REPORTER,
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

    def tick(self) -> None:
        """Run one scheduler tick: pick a project, process one task.

        Respects pause state and capacity limits. Publishes lifecycle
        events to the shared broker.
        """
        decision = self._scheduler.next(self._is_project_runnable)
        if decision is None:
            return

        project_id = decision.project_id
        conn = connect(self._paths.database)
        try:
            init_db(conn)
            self._broker.publish(
                conn, project_id, "tick_scheduled",
                {"project_id": project_id, "reason": decision.reason},
            )

            if self._config is None:
                return

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
                },
            )
        finally:
            conn.close()

    def _is_project_runnable(self, project_id: str) -> bool:
        """Check if a project is runnable: not paused, has ready tasks,
        and capacity is available."""
        if project_id in self._paused:
            return False

        if self._capacity.active_count() >= 4:
            return False
        if self._capacity.active_count(project_id=project_id) >= 2:
            return False

        # Check for ready tasks in the database
        conn = connect(self._paths.database)
        try:
            init_db(conn)
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
        conn = connect(self._paths.database)
        try:
            init_db(conn)
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
                "active_tasks": self._capacity.active_count(),
                "shutdown_requested": self._shutdown.is_set(),
            }
        finally:
            conn.close()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def is_shutdown_requested(self) -> bool:
        return self._shutdown.is_set()
