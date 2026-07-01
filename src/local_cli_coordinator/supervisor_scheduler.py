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


@dataclass(frozen=True)
class ProjectSkipForecast:
    """Dry-run skip reason for a project that would not be scheduled."""

    project_id: str
    reason: str


def simulate_scheduler_round(
    scheduler: "FairProjectScheduler",
    is_runnable: Callable[[str], bool],
    *,
    rounds: int = 1,
) -> list[ScheduleDecision | ProjectSkipForecast]:
    """Project scheduler outcomes without claiming tasks or acquiring leases."""
    outcomes: list[ScheduleDecision | ProjectSkipForecast] = []
    seen: set[str] = set()
    for _ in range(max(1, rounds)):
        decision = scheduler.next(is_runnable)
        if decision is None:
            break
        if decision.project_id in seen:
            continue
        seen.add(decision.project_id)
        if is_runnable(decision.project_id):
            outcomes.append(decision)
        else:
            outcomes.append(
                ProjectSkipForecast(
                    project_id=decision.project_id,
                    reason="not runnable",
                )
            )
    return outcomes


def forecast_project_skip_reason(
    *,
    project_id: str,
    paused_projects: set[str] | None = None,
    stopped_projects: set[str] | None = None,
    capacity_available: bool = True,
    has_claimable_task: bool = False,
    has_runnable_autonomy: bool = False,
    circuit_breaker_reason: str | None = None,
) -> str | None:
    """Return a skip reason when a project would not be scheduled."""
    paused = paused_projects or set()
    stopped = stopped_projects or set()
    if project_id in stopped:
        return "project is stopped"
    if project_id in paused:
        return "project is paused"
    if circuit_breaker_reason:
        return circuit_breaker_reason
    if not capacity_available:
        return "capacity limit reached"
    if has_claimable_task:
        return None
    if has_runnable_autonomy:
        return None
    return "no runnable work"


class FairProjectScheduler:
    """Round-robin scheduler that skips non-runnable projects.

    Policy evaluation (budgets, concurrency, circuit breakers) happens
    outside this class via the is_runnable callback.
    """

    def __init__(self, project_ids: list[str]) -> None:
        self._order: deque[str] = deque(project_ids)
        self._known: set[str] = set(project_ids)

    def register_project(self, project_id: str) -> None:
        """Add a project to the scheduler if not already known."""
        if project_id not in self._known:
            self._known.add(project_id)
            self._order.append(project_id)

    def next(self, is_runnable: Callable[[str], bool]) -> ScheduleDecision | None:
        """Return the next runnable project, or None if none are runnable."""
        for _ in range(len(self._order)):
            project_id = self._order[0]
            self._order.rotate(-1)
            if is_runnable(project_id):
                return ScheduleDecision(project_id)
        return None
