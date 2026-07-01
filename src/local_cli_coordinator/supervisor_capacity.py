"""Shared Supervisor capacity enforcement.

Tracks running tasks across projects with global, per-project, and
daily budget limits. Uses database transactions for consistency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import sqlite3


@dataclass(frozen=True)
class CapacityLease:
    """Immutable record of an acquired capacity slot."""

    project_id: str
    task_id: str
    agent_id: str


class SharedCapacity:
    """Enforces shared capacity limits across projects.

    Limits:
    - max_global_running: total concurrent tasks across all projects
    - max_per_project: concurrent tasks per project
    - max_daily_tasks: total tasks started today (budget)
    """

    def __init__(
        self,
        *,
        max_global_running: int = 4,
        max_per_project: int = 2,
        max_daily_tasks: int = 100,
    ) -> None:
        self._max_global = max_global_running
        self._max_per_project = max_per_project
        self._max_daily = max_daily_tasks
        self._leases: dict[str, CapacityLease] = {}  # task_id → lease
        self._daily_count = 0
        self._daily_date = date.today()

    def try_acquire(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        task_id: str,
        agent_id: str,
    ) -> bool:
        """Try to acquire a capacity slot. Returns True if acquired."""
        self._maybe_reset_daily()

        # Check daily budget
        if self._daily_count >= self._max_daily:
            return False

        # Check global limit
        if len(self._leases) >= self._max_global:
            return False

        # Check per-project limit
        project_active = sum(
            1 for l in self._leases.values() if l.project_id == project_id
        )
        if project_active >= self._max_per_project:
            return False

        self._leases[task_id] = CapacityLease(
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
        )
        self._daily_count += 1
        return True

    def release(self, task_id: str) -> None:
        """Release a capacity slot."""
        self._leases.pop(task_id, None)

    def active_count(self, *, project_id: str | None = None) -> int:
        """Count active leases, optionally filtered by project."""
        if project_id is None:
            return len(self._leases)
        return sum(1 for l in self._leases.values() if l.project_id == project_id)

    def can_accept_project(self, project_id: str) -> bool:
        """Return True if the project is below its per-project concurrency limit."""
        return self.active_count(project_id=project_id) < self._max_per_project

    def snapshot(self) -> dict[str, int]:
        """Return a read-only view of current in-memory capacity usage."""
        self._maybe_reset_daily()
        return {
            "active_global": len(self._leases),
            "max_global": self._max_global,
            "max_per_project": self._max_per_project,
            "daily_used": self._daily_count,
            "max_daily": self._max_daily,
        }

    def forecast_pressure(
        self,
        *,
        project_id: str | None = None,
        additional_tasks: int = 0,
    ) -> dict[str, object]:
        """Forecast capacity pressure without acquiring leases."""
        snap = self.snapshot()
        active_global = int(snap["active_global"])
        max_global = int(snap["max_global"])
        max_per_project = int(snap["max_per_project"])
        daily_used = int(snap["daily_used"])
        max_daily = int(snap["max_daily"])

        projected_global = active_global + max(0, additional_tasks)
        projected_daily = daily_used + max(0, additional_tasks)
        project_active = self.active_count(project_id=project_id) if project_id else 0
        projected_project = project_active + max(0, additional_tasks)

        pressure = "none"
        reasons: list[str] = []
        if projected_daily >= max_daily:
            pressure = "exhausted"
            reasons.append("daily task budget would be exhausted")
        elif projected_global >= max_global:
            pressure = "high"
            reasons.append("global concurrency would be saturated")
        elif projected_project >= max_per_project:
            pressure = "high"
            reasons.append("per-project concurrency would be saturated")
        elif projected_daily >= max(1, int(max_daily * 0.8)):
            pressure = "moderate"
            reasons.append("daily budget nearing limit")

        return {
            "pressure": pressure,
            "reasons": reasons,
            "snapshot": snap,
            "project_active": project_active,
            "forecast": True,
        }

    @classmethod
    def from_running_tasks(
        cls,
        conn: sqlite3.Connection,
        *,
        max_global_running: int = 4,
        max_per_project: int = 2,
        max_daily_tasks: int = 100,
    ) -> "SharedCapacity":
        """Build a capacity view seeded from durable running tasks only."""
        capacity = cls(
            max_global_running=max_global_running,
            max_per_project=max_per_project,
            max_daily_tasks=max_daily_tasks,
        )
        rows = conn.execute(
            """
            select t.id as task_id, t.project_id, l.agent_id
            from tasks t
            join task_leases l on l.task_id = t.id
            where t.state = 'running' and l.released_at is null
            """
        ).fetchall()
        for row in rows:
            capacity._leases[str(row["task_id"])] = CapacityLease(
                project_id=str(row["project_id"]),
                task_id=str(row["task_id"]),
                agent_id=str(row["agent_id"]),
            )
        daily_row = conn.execute(
            """
            select coalesce(sum(tasks_processed), 0) as total
            from daemon_runs
            where date(started_at) = date('now')
            """
        ).fetchone()
        capacity._daily_count = int(daily_row["total"])
        return capacity

    def _maybe_reset_daily(self) -> None:
        today = date.today()
        if today != self._daily_date:
            self._daily_count = 0
            self._daily_date = today
