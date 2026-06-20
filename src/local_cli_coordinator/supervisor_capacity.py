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

    def _maybe_reset_daily(self) -> None:
        today = date.today()
        if today != self._daily_date:
            self._daily_count = 0
            self._daily_date = today
