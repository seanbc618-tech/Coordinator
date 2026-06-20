"""Multi-project Supervisor loop.

Composes the scheduler, event broker, capacity enforcer, and method
registry into a single process that serves multiple project loops
over a Unix socket.
"""

from __future__ import annotations

import threading
from typing import Any

from .db import connect, init_db
from .project_runtime import ProjectRuntime, run_project_cycle, ProjectCycleResult
from .reporting import NULL_REPORTER, Reporter
from .runtime_paths import RuntimePaths
from .supervisor_capacity import SharedCapacity
from .supervisor_events import EventBroker
from .supervisor_methods import SupervisorMethods
from .supervisor_protocol import RequestEnvelope, ResponseEnvelope
from .supervisor_scheduler import FairProjectScheduler


class MultiProjectSupervisor:
    """Manages multiple project loops under one process.

    Owns the socket thread, scheduler tick loop, worker executor,
    and graceful shutdown.
    """

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        scheduler: FairProjectScheduler,
        broker: EventBroker,
        capacity: SharedCapacity,
        methods: SupervisorMethods,
        reporter: Reporter = NULL_REPORTER,
    ) -> None:
        self._paths = paths
        self._scheduler = scheduler
        self._broker = broker
        self._capacity = capacity
        self._methods = methods
        self._reporter = reporter
        self._shutdown = threading.Event()
        self._active_tasks: dict[str, str] = {}  # task_id → project_id

    def tick(self) -> None:
        """Run one scheduler tick: pick a project, process one task."""
        decision = self._scheduler.next(self._is_project_runnable)
        if decision is None:
            return

        conn = connect(self._paths.database)
        try:
            init_db(conn)
            self._broker.publish(
                conn,
                decision.project_id,
                "tick_scheduled",
                {"project_id": decision.project_id, "reason": decision.reason},
            )
        finally:
            conn.close()

    def _is_project_runnable(self, project_id: str) -> bool:
        """Check if a project has ready tasks and capacity is available."""
        if self._capacity.active_count() >= 4:
            return False
        if self._capacity.active_count(project_id=project_id) >= 2:
            return False
        return True

    def status(self) -> dict[str, Any]:
        """Return diagnostic status."""
        conn = connect(self._paths.database)
        try:
            init_db(conn)
            projects = {}
            # List all known projects from tasks
            rows = conn.execute(
                "select distinct project_id from tasks"
            ).fetchall()
            for row in rows:
                pid = row["project_id"]
                from .db import project_task_counts
                projects[pid] = project_task_counts(conn, project_id=pid)

            return {
                "projects": projects,
                "active_tasks": len(self._active_tasks),
                "shutdown_requested": self._shutdown.is_set(),
            }
        finally:
            conn.close()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def is_shutdown_requested(self) -> bool:
        return self._shutdown.is_set()
