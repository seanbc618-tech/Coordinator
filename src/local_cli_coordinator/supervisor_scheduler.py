"""Fair project scheduler for multi-project Supervisor.

Implements round-robin scheduling across registered projects, skipping
paused and circuit-broken projects.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ScheduleDecision:
    """Immutable scheduling decision."""

    project_id: str
    reason: str = "ready"


class FairProjectScheduler:
    """Round-robin scheduler that skips non-runnable projects.

    Policy evaluation (budgets, concurrency, circuit breakers) happens
    outside this class via the is_runnable callback.
    """

    def __init__(self, project_ids: list[str]) -> None:
        self._order: deque[str] = deque(project_ids)

    def next(self, is_runnable: Callable[[str], bool]) -> ScheduleDecision | None:
        """Return the next runnable project, or None if none are runnable."""
        for _ in range(len(self._order)):
            project_id = self._order[0]
            self._order.rotate(-1)
            if is_runnable(project_id):
                return ScheduleDecision(project_id)
        return None
