"""Multi-project Supervisor loop.

Composes the scheduler, event broker, capacity enforcer, and method
registry into a single process. Uses a thread pool for concurrent
project execution. Integrates with the existing engine pipeline.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from typing import Any, Generator

from .autonomous_runs import project_has_runnable_run_session
from .autonomy_runtime import (
    project_autonomy_enabled,
    run_project_autonomy_session,
)
from .config import CoordinatorConfig
from .db import (
    claim_project_ready_task,
    connect,
    get_task,
    init_db,
    list_task_events,
    project_task_counts,
    release_task_lease,
)
from .project_runtime import ProjectRuntime, project_is_runnable, run_project_cycle
from .projects import list_projects
from .event_stream_reporter import wrap_reporter
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
        self._worker_counter = 0
        self._counter_lock = threading.Lock()

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

        self._refresh_projects()

        decision = self._scheduler.next(self._is_project_runnable)
        if decision is None:
            return

        project_id = decision.project_id
        claimed_task_id: str | None = None
        task_key: str | None = None
        agent_id: str | None = None
        submitted = False

        try:
            with self._get_conn() as conn:
                run_project_autonomy_session(
                    conn,
                    project_id=project_id,
                    config=self._config,
                    paths=self._paths,
                    broker=self._broker,
                    paused_projects=self._paused,
                    stopped_projects=self._stopped,
                )

                task, agent_id = claim_project_ready_task(
                    conn, project_id, self._config
                )
                if task is None or agent_id is None:
                    return
                claimed_task_id = task["id"]

                with self._counter_lock:
                    self._worker_counter += 1
                    task_key = f"cycle-{self._worker_counter}"

                if not self._capacity.try_acquire(
                    conn,
                    project_id=project_id,
                    task_id=task_key,
                    agent_id=agent_id,
                ):
                    release_task_lease(conn, claimed_task_id)
                    return

                self._broker.publish(
                    conn, project_id, "tick_scheduled",
                    {"project_id": project_id, "reason": decision.reason},
                )

            future = self._executor.submit(
                self._run_project_cycle,
                project_id,
                task_key,
                agent_id,
                claimed_task_id,
            )
            submitted = True
            with self._futures_lock:
                self._active_futures[task_key] = future
        except Exception:
            if not submitted and claimed_task_id is not None:
                self._abort_tick_claim(claimed_task_id, task_key)
            raise

        # Clean up completed futures
        with self._futures_lock:
            for key in list(self._active_futures):
                if self._active_futures[key].done():
                    del self._active_futures[key]

    def _abort_tick_claim(self, task_id: str, task_key: str | None) -> None:
        """Release a pre-claimed lease when tick fails before worker handoff."""
        with self._get_conn() as conn:
            release_task_lease(conn, task_id)
        if task_key is not None:
            self._capacity.release(task_key)

    def _run_project_cycle(
        self,
        project_id: str,
        task_key: str,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Run a project cycle in a worker thread."""
        with self._get_conn() as conn:
            try:
                runtime = ProjectRuntime(
                    project_id=project_id,
                    repo_root=self._paths.data_dir,
                    state_root=self._paths.state_dir,
                    config=self._config,
                )

                stream_reporter = wrap_reporter(
                    self._reporter,
                    broker=self._broker,
                    conn=conn,
                    project_id=project_id,
                )
                result = run_project_cycle(
                    conn,
                    runtime,
                    stream_reporter,
                    agent_id=agent_id,
                    task_id=task_id,
                )

                if result.task_id and result.tasks_processed:
                    task = get_task(conn, result.task_id)
                    if task is not None and task["state"] in {
                        "done",
                        "failed",
                        "blocked",
                        "needs_split",
                        "awaiting_human",
                        "rejected",
                    }:
                        events = list_task_events(conn, result.task_id)
                        note = events[-1]["note"] if events else ""
                        self._broker.publish(
                            conn,
                            project_id,
                            "task.done",
                            {
                                "task_id": result.task_id,
                                "result": task["state"],
                                "reason": note,
                            },
                        )

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
                release_task_lease(conn, task_id)
                self._capacity.release(task_key)

    def _is_project_runnable(self, project_id: str) -> bool:
        """Check if a project is runnable: not paused/stopped, has ready
        tasks, and capacity is available."""
        if project_id in self._paused:
            return False
        if project_id in self._stopped:
            return False

        if not self._capacity.can_accept_project(project_id):
            return False

        with self._get_conn() as conn:
            if self._capacity.active_count() >= self._executor._max_workers:
                return False
            if project_is_runnable(conn, self._config, project_id):
                return True
            if not project_autonomy_enabled(
                conn, project_id=project_id, config=self._config
            ):
                return False
            return project_has_runnable_run_session(conn, project_id=project_id)

    def _refresh_projects(self) -> None:
        """Discover new projects from tasks and the projects registry."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "select distinct project_id from tasks"
            ).fetchall()
            for row in rows:
                self._scheduler.register_project(row["project_id"])
            for row in list_projects(conn):
                self._scheduler.register_project(row["id"])

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

    def join_workers(self, timeout: float = 30.0, *, shutdown: bool = False) -> bool:
        """Wait for all active workers to complete.

        Returns True if all workers finished, False if some timed out.
        When *shutdown* is True, also stops the executor (final process exit).
        """
        with self._futures_lock:
            pending = list(self._active_futures.items())
        all_done = True
        completed_keys: list[str] = []
        per_future_timeout = timeout / max(len(pending), 1)
        for task_key, future in pending:
            try:
                future.result(timeout=per_future_timeout)
                completed_keys.append(task_key)
            except FutureTimeoutError:
                log.warning("worker timed out after %.1fs", per_future_timeout)
                all_done = False
            except Exception:
                log.exception("worker raised an exception")
                completed_keys.append(task_key)
        with self._futures_lock:
            for task_key in completed_keys:
                self._active_futures.pop(task_key, None)
        if shutdown:
            self._executor.shutdown(wait=all_done)
        return all_done
