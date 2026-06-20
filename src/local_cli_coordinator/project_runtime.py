"""Project runtime adapter for multi-project Supervisor.

Wraps the existing single-project engine cycle with project-scoped
database operations and path isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .config import CoordinatorConfig
from .db import (
    project_next_ready_task,
    project_task_counts,
)
from .reporting import NULL_REPORTER, Reporter


@dataclass(frozen=True)
class ProjectRuntime:
    """Immutable runtime context for a single project."""

    project_id: str
    repo_root: Path
    state_root: Path
    config: CoordinatorConfig

    @property
    def runs_dir(self) -> Path:
        return self.state_root / "projects" / self.project_id / "runs"


@dataclass(frozen=True)
class ProjectCycleResult:
    """Result of one project-scoped daemon cycle."""

    project_id: str
    tasks_processed: int = 0
    failures: int = 0
    stop_reason: str | None = None


def run_project_cycle(
    conn: sqlite3.Connection,
    runtime: ProjectRuntime,
    reporter: Reporter = NULL_REPORTER,
) -> ProjectCycleResult:
    """Run one daemon cycle scoped to a single project.

    This is a thin wrapper that queries project-scoped state and
    delegates to the existing engine for actual task processing.
    """
    from .engine import run_daemon_cycle

    # Check project has ready tasks
    counts = project_task_counts(conn, project_id=runtime.project_id)
    ready = counts.get("ready", 0)
    if ready == 0:
        return ProjectCycleResult(
            project_id=runtime.project_id,
            stop_reason="no ready tasks",
        )

    # Run the existing daemon cycle (it operates on the full DB,
    # but we only have tasks for this project due to scoping)
    result = run_daemon_cycle(
        conn,
        runtime.config,
        runtime.repo_root,
        reporter=reporter,
    )

    return ProjectCycleResult(
        project_id=runtime.project_id,
        tasks_processed=result.tasks_processed,
        failures=result.failures,
        stop_reason=result.stop_reason,
    )
